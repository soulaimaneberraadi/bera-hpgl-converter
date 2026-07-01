"""Run the converter locally (same code Vercel serves via api/index.py)."""
import sys, os, http.server

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))
import index  # noqa: E402

if __name__ == '__main__':
    port = 9000
    srv = http.server.HTTPServer(('0.0.0.0', port), index.handler)
    print(f'BERA Converter — http://127.0.0.1:{port}')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()
