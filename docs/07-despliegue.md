# Despliegue de Vigia (servidor local)

> Estado: **documentado y con pruebas de la parte de backup**. El
> `Dockerfile`/compose **no se han ejecutado en el PC de desarrollo porque
> Docker no está instalado** (2026-10-28). El servicio systemd y el script de
> NSSM tampoco se han arrancado aquí; los comandos son los estándar y llevan
> notas de comprobación. No des por hecho que funcionan hasta pasar la
> checklist del final en la máquina destino.

## 1. ¿Dónde ponerlo?

| Opción | Ventajas | Problemas | Cuándo |
|---|---|---|---|
| **PC con la RTX 3060** | GPU y webcam ya funcionan; 0 configuración extra; es donde se midió (~9 FPS con pose+caras) | Debe quedar encendido; ruido/consumo; si se apaga no hay vigilancia | Ahora, con webcam USB |
| **VM en Proxmox con GPU passthrough** | Aísla el servicio, snapshots, arranca con el host | Hay que pasar la GPU a la VM (IOMMU/VFIO); la webcam USB por passthrough es frágil; el host no puede usar la GPU | Cuando haya cámaras IP (bloque 5) y el PC no sea el servidor |
| **LXC en Proxmox** | Más ligero que una VM | GPU y `/dev/video*` requieren mapear dispositivos y cgroups; CUDA dentro de LXC da más guerra | Solo si controlas bien LXC |
| **Mini-PC / Raspberry** | Bajo consumo | Sin CUDA; YOLO+pose en CPU baja a pocos FPS; no sirve para varias cámaras | Nunca para inferencia pesada |

Recomendación: **PC con GPU para la webcam actual**; mover a una VM de
Proxmox cuando las cámaras sean IP y el procesamiento pueda vivir sin USB.
El contenedor Docker en Windows no ve la webcam con facilidad (Docker Desktop
usa una VM Linux; haría falta USB/IP). En Windows, usa NSSM.

## 2. Antes de arrancar

1. Copia la configuración y los secretos:
   ```bash
   cp config.example.yaml config.yaml
   cp .env.example .env
   ```
2. Rellena `.env` como mínimo con `VIGIA_TOKEN` (cadena larga y aleatoria).
   Sin token, Vigia **se niega a escuchar en red** (solo `127.0.0.1`).
3. Revisa `config.yaml`: `host`, `puerto`, `camara`, retención de clips/BD y
   `telegram` si lo vas a usar. Casi todo se cambia luego desde el panel.
4. El primer arranque descarga los modelos de YOLO (`yolo11n*.pt`) y, si
   activas caras, los ONNX de YuNet/SFace (~40 MB) en `modelos/`. Necesita
   internet una vez; después funciona sin conexión.

## 3. Docker (recomendado en Linux con GPU)

```bash
cp .env.example .env          # edita VIGIA_TOKEN
cp config.example.yaml config.yaml
docker compose up -d --build
docker compose logs -f vigia
```

- La imagen parte de `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` y corre
  como usuario **no root** (`vigia`, UID 1000).
- El compose monta **el proyecto entero** en `/app` para que `config.yaml`,
  `vigia.db`, `clips/`, `logs/`, `modelos/` y `.env` persistan. Es importante:
  el guardado atómico de `config.yaml` no funciona si se monta ese fichero
  suelto (el `rename` da `EBUSY`).
- Publica solo `127.0.0.1:8000`; delante va Caddy o la VPN (apartado 6).
- GPU: el bloque `deploy.resources` pide todas las GPUs. Requiere
  `nvidia-container-toolkit` y el driver de NVIDIA en el host.
- Webcam: descomenta en `docker-compose.yml`:
  ```yaml
  devices:
    - /dev/video0:/dev/video0
  group_add:
    - video
  ```
  y comprueba el GID real con `getent group video`.
- Si tu usuario del host no es UID 1000, añade `user: "${UID}:${GID}"` al
  servicio o ajusta propietarios (`chown -R 1000:1000 .`).

Comprobar (sin abrir el panel a internet):

```bash
curl -I http://127.0.0.1:8000/          # debe dar 200
docker compose ps                       # healthy
docker compose logs --tail=50 vigia
```

### Variante CPU (sin GPU)

Sirve para probar el panel o con muy pocas cámaras, no para producción:

```bash
docker build --build-arg BASE_IMAGE=python:3.12-slim -t vigia:cpu .
docker run -d --name vigia --restart unless-stopped \
  -p 127.0.0.1:8000:8000 --env-file .env \
  -e VIGIA_HOST=0.0.0.0 \
  -v "$PWD":/app \
  vigia:cpu
```

Sin CUDA, ultralytics instala PyTorch CPU. Los FPS serán mucho menores; no hay
medición de CPU en este repositorio todavía (el bloque de GPUs mide GPU).

## 4. systemd (Linux sin Docker)

```bash
sudo useradd --system --home /opt/vigia --shell /usr/sbin/nologin vigia
sudo usermod -aG video vigia
sudo mkdir -p /opt/vigia
sudo cp -r . /opt/vigia            # excluye .venv si es de otra máquina
cd /opt/vigia
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# PyTorch CUDA según el README (índice cu124), no el wheel CPU por defecto.
sudo chown -R vigia:vigia /opt/vigia
sudo cp deploy/vigia.service /etc/systemd/system/vigia.service
sudo systemctl daemon-reload
sudo systemctl enable --now vigia
systemctl status vigia
journalctl -u vigia -f
```

El unit fija `WorkingDirectory=/opt/vigia`, carga `/opt/vigia/.env` y reinicia
si el proceso muere. Si tu distro no tiene grupo `video`, quita
`SupplementaryGroups=video` y da permisos al dispositivo por udev.

## 5. Windows con NSSM

1. Descarga NSSM (<https://nssm.cc>) y descomprime `nssm.exe`.
2. Crea el `.venv`, instala dependencias y prueba a mano:
   ```powershell
   .\.venv\Scripts\python.exe -m vigia.web
   ```
3. Abre PowerShell **como Administrador** y ejecuta:
   ```powershell
   .\deploy\instalar-servicio-windows.ps1 -Ruta "D:\Vigia" `
     -Python "D:\Vigia\.venv\Scripts\python.exe" `
     -Nssm "C:\tools\nssm\nssm.exe"
   ```
4. Comprueba:
   ```powershell
   Get-Service Vigia
   Get-Content .\logs\servicio.err.log -Tail 30
   ```
5. Para actualizar el servicio tras cambiar el código: `Restart-Service Vigia`.
   Para reinstalar con otros parámetros: `-Force` (detiene y elimina el
   anterior; no lo uses sin querer).

El servicio arranca con el usuario que lo instala. Si la webcam falla con
"no disponible" en sesión de servicio, revisa que ese usuario tenga acceso a
la cámara y que el equipo no esté bloqueado; como alternativa, deja el panel
en el usuario de escritorio con el Programador de tareas.

## 6. HTTPS y acceso remoto (Caddy + VPN)

Vigia habla HTTP y el token viaja en la cookie; **no lo expongas tal cual a
internet**. Dos capas:

1. **VPN (recomendado): WireGuard o Tailscale.** Entra por la IP de la VPN
   (`100.x` en Tailscale) y deja `host: 127.0.0.1` o escucha solo en la
   interfaz de la VPN.
2. **Caddy delante** para HTTPS y cabeceras:
   ```bash
   caddy run --config deploy/Caddyfile
   caddy trust        # instala la CA local en tus dispositivos
   ```
   Cambia `vigia.lan` por el nombre que resuelva a la máquina y no publiques
   el puerto 8000 en el router. El `Caddyfile` incluye un ejemplo comentado
   para limitar por `remote_ip` si de verdad necesitas abrirlo.

## 7. Copias de seguridad

`scripts/backup.py` usa la **API de backup de SQLite** (consistente con el WAL
y el servicio escribiendo) y guarda también `config.yaml`. La base incluye la
tabla `rostros` (caras conocidas), así que las caras van dentro del respaldo.
Rota las copias y conserva las N más recientes:

```bash
.venv/bin/python scripts/backup.py --destino backups --conservar 14
# salida: ruta, bytes, nº de eventos, nº de rostros, integridad quick_check
```

Programarlo a diario (Linux):

```cron
17 3 * * * cd /opt/vigia && .venv/bin/python scripts/backup.py --destino /var/backups/vigia --conservar 30 >> logs/backup.log 2>&1
```

Windows (Programador de tareas, diario):

```powershell
D:\Vigia\.venv\Scripts\python.exe D:\Vigia\scripts\backup.py --destino D:\copias\vigia --conservar 30
```

Restaurar:

1. Para el servicio (`systemctl stop vigia` / `nssm stop Vigia`).
2. Guarda la BD actual por si acaso y copia la elegida:
   ```bash
   cp backups/vigia-20261028-031700.db vigia.db
   rm -f vigia.db-wal vigia.db-shm
   cp backups/vigia-20261028-031700-config.yaml config.yaml
   ```
3. Arranca y comprueba `/salud` y que aparecen los eventos y las caras.
4. Las copias incluyen datos biométricos (embeddings de caras): protégelas
   como el resto de la BD y bórralas cuando ya no hagan falta.

## 8. Checklist en la máquina destino

- [ ] `VIGIA_TOKEN` definido y distinto del de ejemplo; `.env` con permisos
      restringidos (no está en git).
- [ ] Vigia escucha en `127.0.0.1` o solo en la VPN; el 8000 **no** está
      abierto en el router.
- [ ] HTTPS con Caddy (o acceso exclusivo por VPN) y aviso de HTTP sin cifrar
      desaparecido en el panel.
- [ ] Servicio con arranque automático y reinicio tras reiniciar el equipo.
- [ ] Panel accesible, login con token, vídeo en vivo y línea del tiempo.
- [ ] Cámara correcta (`/camaras`), zona horaria del sistema bien (las franjas
      y los eventos usan hora local).
- [ ] Telegram: `/telegram/estado` configurado y `/telegram/prueba` llega.
- [ ] Retención de eventos/clips y espacio libre (`/salud`) razonables.
- [ ] `scripts/backup.py` ejecutado a mano y programado; **restauración
      probada** una vez.
- [ ] Logs en `logs/vigia.log` y sin errores repetidos.
- [ ] Tras reiniciar la máquina, el panel vuelve solo y la cámara abre.
