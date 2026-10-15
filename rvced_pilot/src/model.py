from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np

from .common import Blocked, ROOT, canonical, code_fingerprint, dependencies, digest, file_hash, normalize_scores
from .media import tensor_hash, verify_channels

SYSTEM = ('You are Qwen, a virtual human developed by the Qwen Team, Alibaba Group, capable of '
          'perceiving auditory and visual inputs, as well as generating text and speech.')


def model_identity(c):
    local = c['model'].get('local_path')
    if not local or not Path(local).is_dir():
        raise Blocked('Local Qwen2.5-Omni-7B weight directory missing; automatic downloads are disabled')
    local = Path(local).resolve()
    weights = sorted(local.glob('*.safetensors')) + sorted(local.glob('pytorch_model*.bin'))
    if not weights or not (local / 'config.json').exists():
        raise Blocked('No complete Qwen model weight/config files found in configured directory')
    selected = sorted(set(weights + [p for p in local.iterdir() if p.is_file() and p.suffix in ('.json', '.jinja', '.txt', '.model')]))
    files = {p.name: file_hash(p) for p in selected}
    return dict(id=c['model']['id'], revision=c['model']['revision'], files_sha256=files,
                fingerprint=digest(files), dtype=c['model']['dtype'],
                attention_implementation=c['model']['attention_implementation'],
                dependencies=dependencies(), adapter_code_sha256=code_fingerprint(),
                inference_class='Qwen2_5OmniThinkerForConditionalGeneration')


def render_prompt(prompt, text, codes):
    names = ['负向', '中性', '正向']
    return prompt.format(mapping='，'.join(f'{code}={name}' for code, name in zip(codes, names)),
                         transcript=json.dumps(text, ensure_ascii=False))


def candidate_tokens(tokenizer, prefix, codes, method):
    before = tokenizer.encode(prefix, add_special_tokens=False)
    continuations = []
    for code in codes:
        after = tokenizer.encode(prefix + code, add_special_tokens=False)
        if after[:len(before)] != before:
            raise Blocked('Answer changes the tokenized generation prefix; choose a new fixed scoring config')
        tail = after[len(before):]
        if not tail or tokenizer.decode(tail, skip_special_tokens=False) != code:
            raise Blocked('Candidate answer does not round-trip at actual assistant prefix')
        if method == 'next_token' and len(tail) != 1:
            raise Blocked('Candidate is not a single next token; new run must use sequence_log_likelihood for ALL inputs')
        continuations.append(tail)
    if len(set(tuple(x) for x in continuations)) != 3:
        raise Blocked('Answer codes do not have distinct tokenizations')
    return continuations


def single_chat_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    raise Blocked('Expected exactly one chat generation prefix, got a batched/invalid template result')


class QwenScorer:
    """Real model only. No fake/random/string fallback backend exists."""

    def __init__(self, c, identity, prompt):
        import torch
        from transformers import Qwen2_5OmniThinkerForConditionalGeneration, Qwen2_5OmniProcessor
        from qwen_omni_utils import process_mm_info
        if not torch.cuda.is_available():
            raise Blocked('CUDA GPU unavailable; no CPU replacement experiment')
        self.torch, self.c, self.identity, self.prompt = torch, c, identity, prompt
        self.process_mm_info = process_mm_info
        torch.manual_seed(c['seed'])
        torch.cuda.manual_seed_all(c['seed'])
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cuda.matmul.allow_tf32 = False
        self.processor = Qwen2_5OmniProcessor.from_pretrained(c['model']['local_path'], local_files_only=True)
        sr = self.processor.feature_extractor.sampling_rate
        if sr != c['preprocessing']['audio_sr']:
            raise Blocked(f'Processor expects {sr}Hz; frozen config does not match')
        self.model, self.loading_info = Qwen2_5OmniThinkerForConditionalGeneration.from_pretrained(
            c['model']['local_path'], local_files_only=True, device_map='auto',
            torch_dtype=getattr(torch, c['model']['dtype']),
            attn_implementation=c['model']['attention_implementation'], output_loading_info=True)
        # Official text-output-only API preserves all T/A/V understanding and avoids
        # loading unused speech synthesis modules or their serialized speaker dictionary.
        bad_missing = self.loading_info.get('missing_keys', [])
        bad_unexpected = [k for k in self.loading_info.get('unexpected_keys', []) if k.startswith('thinker.')]
        if bad_missing or bad_unexpected or self.loading_info.get('mismatched_keys'):
            raise Blocked('Checkpoint/thinker code mismatch; refusing randomly initialized or ignored reasoning weights: ' +
                          json.dumps(dict(missing=bad_missing, unexpected=bad_unexpected, mismatched=self.loading_info.get('mismatched_keys')), default=str))
        self.model.eval()
        self.thinker = self.model
        self.sequence_forwards = 0
        self.gpu_seconds = 0.0
        self.last_token_validation = None

    def prepare(self, channels, codes):
        verify_channels(channels)
        p = self.c['preprocessing']
        content = [dict(type='text', text=render_prompt(self.prompt, channels.get('T', {}).get('text', ''), codes))]
        if 'A' in channels:
            audio = np.load(channels['A']['path'], allow_pickle=False)
            if len(audio) / p['audio_sr'] > self.processor.feature_extractor.chunk_length:
                raise Blocked('Audio exceeds processor supported duration; refusing silent truncation')
            content.append(dict(type='audio', audio=audio))
        if 'V' in channels:
            if len(channels['V']['frames']) != p['frames']:
                raise Blocked('Unexpected input frame count')
            content.append(dict(type='video', video=channels['V']['frames'], fps=channels['V']['sample_fps'],
                                min_pixels=p['video_min_pixels'], max_pixels=p['video_max_pixels']))
        messages = [dict(role='system', content=[dict(type='text', text=SYSTEM)]), dict(role='user', content=content)]
        text = single_chat_text(self.processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False))
        tokens = candidate_tokens(self.processor.tokenizer, text, codes, self.c['scoring']['method'])
        audios, images, videos = self.process_mm_info(messages, use_audio_in_video=False)
        if len(audios or []) != int('A' in channels) or len(videos or []) != int('V' in channels):
            raise Blocked('Official utility returned wrong channel count')
        kwargs = dict(text=text, audio=audios, images=images, videos=videos, return_tensors='pt',
                      padding=True, use_audio_in_video=False, sampling_rate=p['audio_sr'], truncation=False)
        if 'V' in channels:
            kwargs['fps'] = channels['V']['sample_fps']
        inputs = self.processor(**kwargs)
        hashes = {}
        if 'T' in channels:
            hashes['T'] = channels['T']['hash']
        if 'A' in channels:
            hashes['A'] = digest({k: tensor_hash(inputs[k]) for k in ('input_features', 'feature_attention_mask') if k in inputs})
        if 'V' in channels:
            hashes['V'] = digest({k: tensor_hash(inputs[k]) for k in ('pixel_values_videos', 'video_grid_thw', 'video_second_per_grid') if k in inputs})
        self.last_token_validation = dict(codes=codes, tokens=tokens, method=self.c['scoring']['method'],
                                         generation_prefix_sha256=digest(text), prefix_length=len(inputs['input_ids'][0]))
        device = self.thinker.device
        inputs = {k: v.to(device=device, dtype=self.model.dtype if v.is_floating_point() else v.dtype)
                  if hasattr(v, 'to') else v for k, v in inputs.items()}
        return inputs, tokens, hashes

    def forward(self, inputs):
        torch = self.torch
        torch.cuda.synchronize()
        start = time.perf_counter()
        self.sequence_forwards += 1  # Count attempted forwards, including exceptions.
        try:
            with torch.inference_mode():
                return self.thinker(**inputs, use_audio_in_video=False, use_cache=False, return_dict=True)
        finally:
            torch.cuda.synchronize()
            self.gpu_seconds += time.perf_counter() - start

    def score(self, channels, codes=None):
        torch = self.torch
        codes = codes or self.c['scoring']['codes']
        inputs, tokens, hashes = self.prepare(channels, codes)
        if self.c['scoring']['method'] == 'next_token':
            out = self.forward(inputs)
            raw = [float(out.logits[0, -1, t[0]].float().item()) for t in tokens]
            del out
        else:
            raw = []
            base_len = inputs['input_ids'].shape[1]
            for tail in tokens:
                extended = dict(inputs)
                candidate = torch.tensor([tail], dtype=inputs['input_ids'].dtype, device=inputs['input_ids'].device)
                extended['input_ids'] = torch.cat([inputs['input_ids'], candidate], dim=1)
                extended['attention_mask'] = torch.cat([inputs['attention_mask'], torch.ones_like(candidate)], dim=1)
                out = self.forward(extended)
                logits = out.logits[0, base_len - 1:base_len + len(tail) - 1].float().log_softmax(-1)
                raw.append(float(logits[torch.arange(len(tail), device=logits.device), candidate[0]].sum().item()))
                del out
        return dict(**normalize_scores(raw), processor_channel_hashes=hashes,
                    token_validation=self.last_token_validation, candidate_lengths=[len(t) for t in tokens])
