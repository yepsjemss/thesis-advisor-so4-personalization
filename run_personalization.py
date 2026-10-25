"""Run only the local SO4 application. Original RAG files are not imported."""
import os
from waitress import serve
from personalization.app import create_app

if __name__ == '__main__':
    app = create_app()
    port = int(os.getenv('SO4_PORT', '5002'))
    print(f'SO4 personalization: http://127.0.0.1:{port}/', flush=True)
    print('MySQL curriculum graph; no Neo4j, RAG, model loading, or API calls.', flush=True)
    serve(app, host='127.0.0.1', port=port, threads=4)
