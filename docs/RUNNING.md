# 运行与部署

所有命令从仓库根目录执行；Linux GPU运行时进入rvced_pilot。
本轮主模型已实际运行，发布代码不包含媒体/权重/原始冻结配置。

## 本地检查

```bash
python -m pip install -r rvced_pilot/requirements-cpu.txt
python -m pytest rvced_pilot/tests -q -p no:tmpdir -p no:cacheprovider
```

单元测试使用独立合成夹具，仅用于代码校验，不计入实验结果。

## 配置与真实GPU运行

需要FFmpeg/FFprobe、可用CUDA PyTorch、Qwen2.5-Omni官方权重及CH-SIMS v2.0原始CSV/视频。
本轮实际使用Python3.12.3、Torch2.3.0+cu121、Transformers4.53.3、accelerate1.6.0、qwen-omni-utils0.0.4。
镜像的CUDA PyTorch保留在独立venv中；首次新环境可按官方PyTorch兼容性安装相应CUDA版本。

```bash
cd rvced_pilot
python -m pip install -r requirements-gpu.txt
python src/download_weights.py --destination /path/to/Qwen2.5-Omni-7B --revision ae9e1690543ffd5c0221dc27f79834d0294cba00
python src/configure.py --labels /path/to/CH-SIMS-v2/meta.csv --media-root /path/to/CH-SIMS-v2/Raw --model /path/to/Qwen2.5-Omni-7B --out configs/gpu.yaml
python src/run_gpu_pilot.py --config configs/gpu.yaml
```

CSV字段为video_id、clip_id、text、label、label_T、label_A、label_V、mode；路径Raw/{video_id}/{clip_id}.mp4。
原始数据不覆盖，CSV标签不与特征pickle标签混用。原项目test已经用于早期探索，不能再声称新的未见确认性测试。
用户需自行取得数据；本仓库不替代数据授权或提供第三方媒体镜像。

runner先盘点、生成审核材料、真实冒烟；只有候选打分、通道隔离和重复检查通过，主pilot才可冻结。
固定清单、提示、配置、权重/依赖/代码指纹后才推理；供体不按模型输出选择，失败不能靠换供体补齐。
T独立文本，A为16kHz单声道float32且不逐片段归一化/拉伸，V为8个均匀无声帧，官方接口use_audio_in_video=false。
候选A/B/C在真实助手生成位置校验单token，读取候选logits归一化，无字符串/随机回退。

## 恢复与新版本

恢复同一Linux路径、相同代码与依赖、私有冻结配置后，在rvced_pilot执行：

```bash
python pilot.py infer --config configs/gpu.yaml --run-dir runs/main-v1 --phase main
python pilot.py analyze --config configs/gpu.yaml --run-dir runs/main-v1
python pilot.py report --config configs/gpu.yaml --run-dir runs/main-v1
```

单输入原子缓存先落盘后追加日志；恢复不会重选供体。代码、配置或路径改变要新建版本，不覆盖旧冻结文件。
原始main-a800-v3精确运行目录、分数缓存和失败v1/v2保留在私有归档。
原模型总上限1,100个唯一评分键；真实序列前向、GPU同步墙钟、显存与失败分开计数。
本阶段不训练、不做LoRA，第二模型没有运行。
