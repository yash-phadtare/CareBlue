"""WSGI entry point. Use flask --app app db-upgrade before first use."""
from careblue import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False)
