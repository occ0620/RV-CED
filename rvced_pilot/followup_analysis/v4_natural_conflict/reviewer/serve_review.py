from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import functools, threading, urllib.parse, webbrowser
root=Path(__file__).resolve().parent
url="http://127.0.0.1:8774/"+urllib.parse.quote("打开盲审.html")
handler=functools.partial(SimpleHTTPRequestHandler,directory=str(root))
server=ThreadingHTTPServer(("127.0.0.1",8774),handler)
print("盲审地址：",url,flush=True)
threading.Timer(0.5,lambda:webbrowser.open(url)).start()
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:server.server_close()
