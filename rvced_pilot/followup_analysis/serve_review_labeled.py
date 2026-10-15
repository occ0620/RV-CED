"""Serve the Chinese-label overlay; frozen v1 source and rating protocol remain intact."""
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
import argparse
import serve_review as legacy
from common import HERE
from review_ui_labels import labeled_page,revised_guide

def handler():
    old=legacy.PAGE
    try:
        legacy.PAGE=labeled_page(old,'/guide_labeled.md')
        Base=legacy.handler()
    finally:legacy.PAGE=old
    guide=revised_guide((HERE/'reviewer/guide.md').read_text(encoding='utf-8')).encode()
    class LabeledHandler(Base):
        def do_GET(self):
            if urlsplit(self.path).path=='/guide_labeled.md':
                self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Content-Length',str(len(guide)));self.end_headers();self.wfile.write(guide)
            else:super().do_GET()
    return LabeledHandler

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    print(f'CHINESE_LABEL_REVIEW http://127.0.0.1:{a.port}/ (codes and browser storage keys unchanged)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),handler()).serve_forever()
