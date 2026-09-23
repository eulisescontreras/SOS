# Botón SOS

App web mínima (Python + Flask, sin base de datos):

1. **Primera vez:** la persona escribe su nombre e indicaciones y sube archivos (opcional). Todo se guarda en `data/users/<id>/` en el servidor. En el teléfono solo queda guardado su id (localStorage), así que no vuelve a llenar nada. Después puede editar sus datos con "Editar mis datos".
2. **Luego:** la app solo muestra el botón **SOS**. Un toque obtiene las coordenadas GPS y envía una alerta con el nombre, las coordenadas, un link a Google Maps y el link a la página con su información y sus archivos (`/p/<id>?k=<clave de lectura>`).

## Estructura de datos
```
data/
├── outbox.log                 # every alert "sent" (log backend)
└── users/<id>/
    ├── info.json              # name, notes, edit_key, view_key
    ├── alerts.jsonl           # alert history
    └── files/                 # uploaded files
```

## Correr en local
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux: .venv/bin/pip
.venv/Scripts/python app.py                     # http://localhost:5000
.venv/Scripts/pip install pytest && .venv/Scripts/python -m pytest -q
```
En `localhost` el GPS funciona sin HTTPS.

## Variables de entorno (`.env`)
| Variable | Default | Uso |
|---|---|---|
| `BASE_URL` | `http://localhost:5000` | URL pública, para armar el link del correo |
| `ALERT_EMAIL` | vacío | destinatario de las alertas |
| `DATA_DIR` | `./data` | carpeta de datos |
| `MAX_UPLOAD_MB` | `50` | tamaño máximo por envío |
| `NOTIFIER_BACKEND` | `log` | cómo se envía la alerta |

## Envío de correo (pendiente)
Hoy el backend `log` escribe cada alerta en `data/outbox.log` y en la consola. Para enviar correos reales, agrega una función `(to, subject, body)` en `notifier.py`, regístrala en `BACKENDS` y ponle ese nombre a `NOTIFIER_BACKEND`. No hace falta tocar nada más.

## Despliegue en el droplet (DigitalOcean)
⚠️ **El GPS del navegador exige HTTPS.** Se necesita un dominio o subdominio apuntando a la IP del droplet, porque Let's Encrypt no emite certificados para una IP.

```bash
sudo apt install -y python3-venv nginx certbot python3-certbot-nginx
sudo mkdir -p /opt/sos-app && sudo chown $USER /opt/sos-app
# copy the project into /opt/sos-app (git clone / scp)
cd /opt/sos-app && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
printf 'BASE_URL=https://sos.example.com\nALERT_EMAIL=alertas@example.com\n' > .env
sudo mkdir -p data && sudo chown -R www-data:www-data data
sudo cp deploy/sos.service /etc/systemd/system/ && sudo systemctl enable --now sos
sudo cp deploy/nginx-sos.conf /etc/nginx/sites-available/sos   # edit server_name
sudo ln -s /etc/nginx/sites-available/sos /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d sos.example.com
```
Haz un respaldo periódico de `data/`: es la única copia de la información de las personas.
