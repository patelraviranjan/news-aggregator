"""Entry point: `python app.py` for development / gunicorn target."""
from application import create_app

app = create_app()

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 5000))
    # Bind 0.0.0.0 only so that docker/external proxies work; localhost is otherwise the dev default.
    app.run(host="127.0.0.1", port=port, debug=True, use_reloader=False)
