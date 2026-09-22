# AgentBench — Puesta en marcha

Este documento describe cómo poner en marcha AgentBench v0.2 y los entornos
`dbbench-std`, `webshop-std`/`webshop-dev` y `ltp-std` a partir de la
configuración construida en este proyecto.

---

## Instalación inicial

Clona el repositorio e instala las dependencias:

```bash
cd AgentBench
conda create -n agent-bench python=3.9
conda activate agent-bench
pip install -r requirements.txt
```

Asegúrate de que [Docker](https://www.docker.com/) está instalado y corriendo:

```bash
docker ps
```

---

# Entorno DBBench ("standard")

## 0. Requisitos previos

- Docker Desktop instalado y corriendo (la imagen oficial `mysql` se
  descarga automáticamente de Docker Hub al lanzar el Task Server; no hace
  falta construir nada).
- Entorno conda `agent-bench` activado.
- `mysql-connector-python` instalado en el entorno conda. La versión
  utilizada en este proyecto es la 9.x, que introduce cambios en la API
  respecto a versiones anteriores (ver sección de fixes).
- Si el modelo evaluado está servido en un clúster remoto (Ollama / vLLM):
  acceso SSH a esa máquina, y VPN conectada si hace falta para llegar a ella.

## 1. Fixes aplicados al código fuente

El código original de AgentBench v0.2 para este entorno contiene dos bugs
que impiden evaluar correctamente las tareas de tipo INSERT y UPDATE. Ambos
deben corregirse antes de lanzar nada.

### Bug 1 — `multi=True` incompatible con `mysql-connector-python` v9.x

En `src/server/tasks/dbbench/Interaction.py`, el método `execute()` llama a
`cursor.execute(sql, data, multi=True)`. En `mysql-connector-python` v9.x el
parámetro `multi` fue renombrado a `map_results`, y además `execute()` ya no
devuelve un generador iterable sino `None`, por lo que la lógica de consumo
de resultados debe reescribirse usando `cursor.nextset()`.

El método `execute()` corregido queda así:

```python
def execute(
    self,
    sql: str,
    database: str = None,
    data: Union[Sequence, Dict[str, Any]] = (),
) -> Optional[str]:
    self.conn.reconnect()
    try:
        with self.conn.cursor() as cursor:
            if database:
                cursor.execute(f"use `{database}`;")
                cursor.fetchall()
                cursor.nextset()
            cursor.execute(sql, params=data if data else None)
            result = None
            while True:
                if cursor.with_rows:
                    result = cursor.fetchall()
                else:
                    result = f"{cursor.rowcount} row(s) affected"
                if not cursor.nextset():
                    break
            if result is None:
                result = []
            result = str(result)
        self.conn.commit()
    except Exception as e:
        result = str(e)
    if len(result) > 800:
        result = result[:800] + "[TRUNCATED]"
    return result
```

### Bug 2 — `MD5()` de MySQL falla con nombres de tabla con espacios

En `src/server/tasks/dbbench/__init__.py`, la evaluación de tareas INSERT y
UPDATE calcula un hash MD5 del estado final de la tabla mediante una consulta
SQL. El problema es que en AgentBench los nombres de las tablas pueden
contener espacios (por ejemplo, "Team Information" o "Olympic Medal Table"),
y MySQL 8.x interpreta erróneamente la llamada a `MD5()` como una referencia
a una función definida dentro de la base de datos con ese nombre, en lugar de
como la función global del sistema, provocando el error:

```
FUNCTION <nombre_tabla>.MD5 does not exist
```

La solución adoptada consiste en prescindir del cálculo del hash en SQL y
realizarlo directamente en Python con `hashlib`, obteniendo el contenido de
la tabla mediante una consulta `SELECT` simple y replicando la lógica
original. El bloque de evaluación INSERT/UPDATE corregido queda así:

```python
if entry["type"][0] in ("INSERT", "DELETE", "UPDATE"):
    columns = [
        f"`{column['name']}`"
        for column in entry["table"]["table_info"]["columns"]
    ]
    columns_sql = ",".join(columns)
    rows_query = f"SELECT {columns_sql} FROM `{db}`.`{db}`;"
    try:
        import mysql.connector
        import hashlib
        tmp_conn = mysql.connector.connect(
            host="127.0.0.1",
            user="root",
            password=container.password,
            port=container.port,
        )
        with tmp_conn.cursor() as cur:
            cur.execute(rows_query)
            rows = cur.fetchall()
        tmp_conn.close()
        row_hashes = sorted([
            hashlib.md5(
                ",".join(str(c) for c in row if c is not None).encode()
            ).hexdigest()[:5]
            for row in rows
        ])
        final_hash = hashlib.md5(",".join(row_hashes).encode()).hexdigest()
        answer = f"[('{final_hash}',)]"
    except Exception as e:
        answer = str(e)
```

Nótese que el filtro `if c is not None` es necesario para replicar el
comportamiento de `CONCAT_WS` en MySQL, que ignora los valores NULL en lugar
de convertirlos a cadena vacía.

Los archivos corregidos completos se encuentran en:
- `src/server/tasks/dbbench/Interaction.py`
- `src/server/tasks/dbbench/__init__.py`

## 2. Ficheros de configuración usados

```
configs/
├── tasks/
│   ├── dbbench.yaml            # definición de la tarea dbbench-std
│   └── task_assembly_lite.yaml # registra dbbench.yaml junto al resto
├── start_task_lite.yaml        # arranca el Task Server con dbbench-std
└── assignments/
    └── default.yaml            # qué agente evalúa dbbench-std
```

### `dbbench.yaml`

```yaml
default:
  module: src.server.tasks.dbbench.DBBench
  parameters:
    max_round: 5

dbbench-std:
  parameters:
    name: dbbench-std
    data_file: data/dbbench/standard.jsonl
```

### `configs/assignments/default.yaml`

```yaml
import: definition.yaml

concurrency:
  task:
    dbbench-std: 1
  agent:
    <nombre_agente>: 1

assignments:
  - agent:
      - <nombre_agente>
    task:
      - dbbench-std

output: "outputs/{TIMESTAMP}"
```

Sustituye `<nombre_agente>` por el agente definido en `definition.yaml`
que quieras evaluar.

## 3. Infraestructura de modelos (si aplica)

Si el modelo evaluado está en un clúster remoto, levanta el túnel SSH antes
de lanzar nada:

```bash
autossh -M 0 -o ServerAliveInterval=30 -o ServerAliveCountMax=20 \
  -L 11435:127.0.0.1:PUERTO_OLLAMA \
  -L 11436:127.0.0.1:PUERTO_VLLM \
  usuario@servidor -N
```

Verifica que el endpoint responde antes de continuar:

```bash
curl http://localhost:11435/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model": "<modelo>", "messages": [{"role": "user", "content": "hola"}]}'
```

## 4. Arrancar el Task Server y lanzar la evaluación

```bash
# Terminal 1
python -m src.start_task -a --config configs/start_task_lite.yaml
```

Espera a ver los mensajes de arranque del servidor (`Application startup
complete`, heartbeats con `200 OK`) antes de continuar.

```bash
# Terminal 2
python -m src.assigner --config configs/assignments/default.yaml
```

## 5. Resultados

Cada muestra completada queda en
`outputs/<TIMESTAMP>/<agente>/dbbench-std/runs.jsonl`. Al terminar todas
las muestras, el resumen agregado con las métricas queda en `overall.json`
en la misma carpeta.

Las métricas reportadas son:
- `SELECT_accuracy`: precisión en tareas de consulta
- `INSERT_accuracy`: precisión en tareas de inserción
- `UPDATE_accuracy`: precisión en tareas de actualización
- `overall_cat_accuracy`: media de las tres anteriores

## Notas

- Si al lanzar `start_task` aparece el error "address already in use" en
  el puerto 5000 o 5001, mata los procesos que lo ocupan antes de relanzar:
  ```bash
  lsof -ti:5000 | xargs kill -9
  lsof -ti:5001 | xargs kill -9
  ```
- Cualquier cambio en `__init__.py` o `Interaction.py` requiere reiniciar
  el Task Server para que tenga efecto.
- El contenedor MySQL se crea y destruye automáticamente por cada sesión de
  evaluación; no es necesario gestionarlo manualmente.

---

# Entorno WebShop reducido ("lite")

## 0. Requisitos previos

- Docker Desktop instalado y corriendo, con al menos ~25-30 GB de espacio
  libre en disco (la imagen base pesa ~11,5 GB antes de comprimir).
- Entorno conda `agent-bench` activado.
- Si vas a usar modelos servidos en un clúster remoto (Ollama, vLLM): acceso
  SSH a esa máquina y, si hace falta VPN para llegar a ella, tenerla
  conectada durante toda la ejecución.

## 1. Ficheros necesarios para construir la imagen

Todo esto vive en `webshop_confi/` dentro del repo:

```
webshop_confi/
├── webshop-lite.Dockerfile   # receta de la imagen reducida
└── filter_goals.py           # script de filtrado del catálogo
```

> Los ficheros `items_shuffle_1000.json` / `items_ins_v2_1000.json` que
> puedan quedar en esta carpeta son de un enfoque anterior (muestreo
> aleatorio de 1000 productos) y **ya no los usa** `filter_goals.py` — se
> pueden borrar sin problema.

### Qué hace cada uno

- **`webshop-lite.Dockerfile`**: parte de la imagen oficial
  `longinyu/agentbench-webshop` (que trae el catálogo completo de ~1,18M de
  productos), instala `ijson`, ejecuta `filter_goals.py` dentro del build,
  apunta el entorno al catálogo filtrado resultante y reconstruye el índice
  de búsqueda (Lucene) sobre él.
- **`filter_goals.py`**: recorre en *streaming* el catálogo completo
  (`items_shuffle.json`, ~5,2 GB) ya presente en la imagen base y conserva
  únicamente los productos que tienen una instrucción humana asociada en
  `items_human_ins.json` (~12.087 instrucciones del *benchmark* original).
  Genera `items_shuffle_goals.json` e `items_ins_v2_goals.json`, con lo que
  el catálogo pasa de ~1,18M de productos a ~10.136, sin perder práctica­mente
  ninguna de las tareas evaluables.

## 2. Construir la imagen Docker

```bash
cd webshop_confi
docker build --platform linux/amd64 -f webshop-lite.Dockerfile -t agentbench-webshop-lite:local .
```

- Tarda entre 5 y 15 minutos, la mayor parte descargando y extrayendo la
  capa base (~11,5 GB) — si tu conexión es lenta o inestable, lánzalo en
  segundo plano:
  ```bash
  nohup docker build --platform linux/amd64 -f webshop-lite.Dockerfile -t agentbench-webshop-lite:local . > build.log 2>&1 &
  tail -f build.log
  ```
- Verifica al final del log que el reindexado reporta **10.136 documentos
  indexados** (o un número similar) — si sale un número mucho más bajo
  (p. ej. 13), algo ha ido mal en el filtrado.
- Confirma que la imagen quedó creada:
  ```bash
  docker images | grep agentbench-webshop-lite
  ```

### Problemas típicos durante el build

- **Falta de espacio / Docker Desktop se cae**: comprueba `df -h /` y
  `docker system df`; limpia con `docker builder prune -a` antes de
  reintentar. Si el problema persiste sin motivo aparente, revisa si hay
  una actualización de macOS pendiente/descargada
  (`softwareupdate --list`, `tmutil listlocalsnapshots /`), ya que puede
  reservar espacio invisible al `df` normal.
- **Aviso de plataforma** (`linux/amd64` vs `arm64`): normal en Mac Apple
  Silicon, ralentiza el build por emulación pero no lo impide.

## 3. Ficheros de configuración de AgentBench

Todos van en `configs/`, en el repositorio raíz (no dentro de
`webshop_confi/`).

**`configs/tasks/webshop_lite.yaml`** — copia autocontenida de
`webshop.yaml` apuntando a la imagen lite:
```yaml
default:
  module: src.server.tasks.webshop_docker.WebShop
  parameters:
    concurrency: 1
  docker:
    image: agentbench-webshop-lite:local
    command: ln -s /root/webshop /root/workspace/src/server/tasks/webshop_docker;cp /root/workspace/src/server/tasks/webshop/__init__.py /root/webshop/__init__.py;

webshop-dev:
  parameters:
    name: webshop-dev
    start: 200
    end: 280

webshop-std:
  parameters:
    name: webshop-std
    start: 0
    end: 200
```

**`configs/tasks/task_assembly_lite.yaml`**:
```yaml
import:
  - task_assembly.yaml
  - webshop_lite.yaml
```

**`configs/start_task_lite.yaml`**:
```yaml
definition:
  import: tasks/task_assembly_lite.yaml

start:
  webshop-std: 1
```

**Un `configs/assignments/webshop_<modelo>.yaml` por cada modelo**, por
ejemplo:
```yaml
import: definition.yaml

concurrency:
  task:
    webshop-std: 1
  agent:
    <nombre_agente>: 1

assignments:
  - agent:
      - <nombre_agente>
    task:
      - webshop-std

output: "outputs/{TIMESTAMP}"
```
donde `<nombre_agente>` es el que tengas definido en `definition.yaml`
(p. ej. `latxa`, `ollama-qwen3-nothink`, `ollama-qwen3-think`,
`vllm-gemma4`).

## 4. Si el modelo corre en un servidor remoto (Ollama / vLLM)

1. Lanza el servidor en el clúster remoto (script SLURM correspondiente) y
   anota el puerto asignado.
2. Abre un túnel SSH al puerto local que espera el agente
   (`11435` para Ollama, `11436` para vLLM en esta configuración),
   preferiblemente con `autossh` para que se reconecte solo si la conexión
   se cae:
   ```bash
   autossh -M 0 -o "ServerAliveInterval=15" -o "ServerAliveCountMax=3" \
     -L 11435:127.0.0.1:<PUERTO_REMOTO> usuario@servidor -N
   ```
3. Espera a que el servidor remoto confirme que está listo
   (`Application startup complete` en vLLM, o el primer `200 OK` de Ollama)
   antes de lanzar el *assigner*.

## 5. Arrancar el Task Server

```bash
conda activate agent-bench
python -m src.start_task -a --config configs/start_task_lite.yaml
```

Espera a ver en el log la carga del entorno (`Products loaded`,
`Keys cleaned`, `Attributes loaded`) y el primer `heartbeat` con
`200 OK` antes de continuar. Si el log no muestra nada de esto y solo
aparecen heartbeats, revisa que `configs/start_task_lite.yaml` esté
importando `tasks/task_assembly_lite.yaml` (no el `task_assembly.yaml`
original) y que incluya `webshop-std` en su bloque `start:`.

## 6. Lanzar el Assigner

En otra terminal:
```bash
conda activate agent-bench
python -m src.assigner --config configs/assignments/webshop_<modelo>.yaml
```

Los resultados quedan en `outputs/<TIMESTAMP>/runs.jsonl` (por episodio) y
`outputs/<TIMESTAMP>/overall.json` (agregado).

## 7. Verificación rápida de que todo está bien montado

```bash
docker run --rm agentbench-webshop-lite:local \
  conda run -n webshop python -c "from web_agent_site.envs.web_agent_text_env import WebAgentTextEnv as E; e=E(observation_mode='text', human_goals=True); print('goals:', len(e.goals))"
```
Debería imprimir un número en torno a 12.000 (goals humanos disponibles).
Si sale un número mucho más bajo, el filtrado del catálogo no se hizo
correctamente.

---

# Entorno LTP ("standard")

## 0. Requisitos previos

- Docker Desktop instalado y corriendo (la imagen `longinyu/agentbench-ltp`
  se descarga de Docker Hub; no hace falta construir nada).
- Entorno conda `agent-bench` activado.
- Si el modelo evaluado, o el modelo-host, están servidos en el clúster
  remoto (Ollama en un puerto, vLLM en otro): acceso SSH a esa máquina, y
  VPN conectada si hace falta para llegar a ella.

## 1. Ficheros de configuración usados

```
configs/
├── tasks/
│   ├── ltp.yaml            # definición de la tarea ltp-std
│   └── task_assembly.yaml  # registra ltp.yaml junto al resto de entornos
├── agents/
│   ├── api_agents.yaml     # modelos evaluados + modelo-host
│   ├── ollama-think.yaml   # base de agente para Qwen3 con razonamiento
│   └── ollama-nothink.yaml # base de agente para Qwen3 sin razonamiento
└── assignments/
    └── default.yaml        # qué agente evalúa ltp-std
```

> `configs/start_task_lite.yaml` (usado en el paso 4) ya existía en el
> repositorio antes de este trabajo. **Antes de lanzar nada, comprueba a
> qué fichero apunta su `import:`**, ya que en tu repo existen tanto
> `task_assembly.yaml` como `task_assembly_lite.yaml`, y solo el primero
> es el que registra `ltp.yaml`:
> ```bash
> cat configs/start_task_lite.yaml
> ```
> Si `import:` no apunta a `configs/tasks/task_assembly.yaml`, corrígelo
> para que lo haga, o lanza el controlador con un fichero de arranque que
> sí lo importe.

### `ltp.yaml`

```yaml
default:
  module: src.server.tasks.ltp.LateralThinkingPuzzle
  docker:
    image: longinyu/agentbench-ltp
  parameters:
    round: 25
    eval_yaml: "configs/agents/api_agents.yaml"

ltp-std:
  parameters:
    name: ltp-std
    filepath: "data/lateralthinkingpuzzle/standard.xlsx"
```

`round: 25` fija el número máximo de rondas por partida. `eval_yaml`
apunta al mismo fichero de agentes donde se declara el modelo-host, que
`task.py` carga internamente por el nombre fijo `mistral-host`, con
independencia de qué agente se esté evaluando como solver.

### `api_agents.yaml` (entradas relevantes para una corrida individual)

- **`mistral-host`**: el modelo-host. En este proyecto se usa Mistral,
  servido vía Ollama.
- **`latxa`**: solver servido vía API externa (`max_tokens: 4096`).
- **`ollama-qwen3-think`** / **`ollama-qwen3-nothink`**: solvers Qwen3,
  cada uno importando su propio fichero base (`ollama-think.yaml` /
  `ollama-nothink.yaml`), que usan el endpoint nativo `/api/chat` de
  Ollama (no el compatible con OpenAI) para poder controlar el parámetro
  `think`.
- **`vllm-gemma4`**: solver servido vía vLLM.

## 2. Infraestructura de modelos (si aplica)

Si el solver o el host están en el clúster remoto:

```bash
ssh usuario@servidor
squeue -u $USER   # confirmar que el/los job(s) de Ollama y/o vLLM están RUNNING
```

Levanta el túnel (usa `autossh` para que se reconecte solo si la red
falla a mitad de una corrida larga):

```bash
autossh -M 0 -o ServerAliveInterval=30 -o ServerAliveCountMax=20 \
  -L 11435:127.0.0.1:PUERTO_OLLAMA \
  -L 11436:127.0.0.1:PUERTO_VLLM \
  usuario@servidor -N
```

Verifica cada endpoint antes de lanzar nada:

```bash
curl http://localhost:11435/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model": "mistral", "messages": [{"role": "user", "content": "hola"}]}'
```

## 3. Elegir el agente a evaluar

Edita `configs/assignments/default.yaml`, cambiando el nombre del agente
por el que quieras evaluar (debe existir como clave en `api_agents.yaml`):

```yaml
import: definition.yaml

concurrency:
  task:
    ltp-std: 1
  agent:
    ollama-qwen3-think: 1

assignments:
  - agent:
      - ollama-qwen3-think
    task:
      - ltp-std

output: "outputs/{TIMESTAMP}"
```

## 4. Arrancar el controlador y lanzar la evaluación

Dos terminales:

```bash
# Terminal 1
docker stop $(docker ps -q)   # por si quedó un contenedor de una corrida anterior
python -m src.start_task -a --config configs/start_task_lite.yaml
```

```bash
# Terminal 2, una vez el controlador esté listo
python -m src.assigner --config configs/assignments/default.yaml --auto-retry
```

## 5. Resultados

Cada muestra completada queda en
`outputs/<TIMESTAMP>/<agente>/ltp-std/runs.jsonl`. Al terminar todas las
muestras, el resumen agregado con las métricas (QR, SGA, RE, GP) queda en
`overall.json`, en la misma carpeta.

## Notas

- Un cambio en cualquier fichero de configuración o en `task.py` **no se
  aplica en caliente**: para (`Ctrl+C`) y vuelve a lanzar `start_task`.
- Error de "port is already allocated" al lanzar `start_task` → suele ser
  un contenedor de una corrida anterior sin cerrar:
  `docker stop $(docker ps -q)`.