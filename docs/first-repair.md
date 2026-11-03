---
title: Run AEGIS locally and watch it repair a fault
description: Set up AEGIS on a Windows machine, inject a configuration fault into the simulated cluster, and confirm that AEGIS detects, explains and repairs it.
---

# Run AEGIS locally and watch it repair a fault

AEGIS watches a Kubernetes application, finds faults, explains the cause and repairs the faults it can repair safely. This topic shows you how to run AEGIS against its built-in simulated cluster, so you do not need Kubernetes or Docker.

When you finish, you will have broken one service on purpose and watched AEGIS fix it. The whole task takes about 15 minutes.

## Before you begin

Make sure you have:

- Windows 10 or later. The setup commands use the Windows path to the Python virtual environment.
- Python 3.10 or later. To check, run `python --version`.
- Node.js 18 or later, which includes npm. To check, run `node --version`.
- Git.
- Two terminal windows open. AEGIS has two parts that run at the same time: the API and the dashboard.

You do not need an API key. Without one, AEGIS uses saved explanations instead of calling a language model. To get live explanations, see [Use a live language model](#use-a-live-language-model).

## Set up AEGIS

1. Clone the repository and open its folder:

   ```powershell
   git clone https://github.com/rohitpatil9121/AEGIS.git
   cd AEGIS
   ```

2. Create a Python virtual environment for the API and install its packages:

   ```powershell
   python -m venv backend\.venv
   backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
   ```

3. Install the dashboard packages:

   ```powershell
   npm --prefix frontend install
   ```

   If you have `make` installed, `make setup` runs steps 2 and 3 for you.

## Start AEGIS

1. In the first terminal, start the API:

   ```powershell
   backend\.venv\Scripts\python -m uvicorn aegis.api:app --app-dir backend --port 8000
   ```

   Leave this terminal open. To check that the API is running, open <http://localhost:8000/api/state> in a browser. You see JSON that starts with `"t"` and includes `"cluster": "simulated"`.

2. In the second terminal, go to the `AEGIS` folder and start the dashboard:

   ```powershell
   npm --prefix frontend run dev
   ```

3. Open <http://localhost:5173> in a browser. The service graph shows six services, all in the Running state.

## Inject a fault and watch the repair

Fault 1 changes the configuration of the `cart` service so that it points at the wrong Redis port. The service then crashes every time it starts.

1. In the dashboard, click the **Config error** fault, or press **1**.

   To inject it from the command line instead, run this in a third terminal:

   ```powershell
   Invoke-RestMethod -Method Post http://localhost:8000/api/fault/1
   ```

   The response is `ok : True`.

2. Watch the incident in the dashboard. Within about 30 seconds, its status changes in this order:

   1. `detected`: AEGIS sees the `cart` pod in `CrashLoopBackOff`.
   2. `diagnosing`: AEGIS works out the root cause, a ConfigMap regression.
   3. `repairing`: AEGIS reverts the ConfigMap to its last working version.
   4. `resolved`: the `cart` service is running again.

3. Read the explanation attached to the incident. It says that the ConfigMap now points at port 6380 while Redis listens on 6379, and that restarting the pod alone would not fix it.

## Check the result

Open <http://localhost:8000/api/state> again and find the `incidents` list. The latest incident shows:

- `"status": "resolved"`
- `"proposed_action": "revert_configmap"`
- `"level": "L3"` and `"allowed": true`
- An `mttr` value: the number of seconds from detection to repair.

## See a repair that AEGIS refuses

Some repairs would destroy data. AEGIS blocks them and hands the incident to a person.

1. Inject fault 5, which fills the Redis disk:

   ```powershell
   Invoke-RestMethod -Method Post http://localhost:8000/api/fault/5
   ```

2. Wait for the incident to reach the `blocked` status. The guardrail reason reads: "delete_pvc on 'redis-data' is irreversible data loss (L4). Hard-blocked; escalated to a human."

Deleting the volume would clear the disk, but it would also delete the data on it. AEGIS never runs this action on its own.

## Clean up

1. Remove all injected faults. In the dashboard, click **Clean up** or press **C**. Or run:

   ```powershell
   Invoke-RestMethod -Method Post http://localhost:8000/api/cleanup
   ```

2. Press **Ctrl+C** in each terminal to stop the API and the dashboard.

## Use a live language model

To have a language model write each explanation instead of using the saved ones, set a Groq API key before you start the API:

```powershell
$env:GROQ_API_KEY = "<your-key>"
backend\.venv\Scripts\python -m uvicorn aegis.api:app --app-dir backend --port 8000
```

The incident's `model` field then shows `llama-3.1-8b-instant` instead of `cached`. If the call fails, for example because of a rate limit, AEGIS falls back to the saved explanation.

## Fault reference

| Fault | Name | Service | What AEGIS does |
|---|---|---|---|
| 1 | Config error | `cart` | Reverts the ConfigMap |
| 2 | Memory leak | `recommendation` | Restarts the pod |
| 3 | Traffic spike | `frontend` | Scales the deployment |
| 4 | Dependency failure | `redis` | Restarts the pod |
| 5 | Disk full | `redis` | Blocks the repair and escalates to a person |
| 6 | Network latency | `checkout` | Diagnoses the cause and escalates to a person |

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `The system cannot find the path specified` when you start the API | The virtual environment was not created, or you are not in the `AEGIS` folder | Run `cd AEGIS`, then repeat step 2 of [Set up AEGIS](#set-up-aegis). |
| `[Errno 10048]` when you start the API | Another program is using port 8000 | Close that program, or start the API with `--port 8001` and use that port in the URLs above. |
| `curl` returns an error in PowerShell | In PowerShell, `curl` is an alias for a different command | Use `Invoke-RestMethod` as shown, or run `curl.exe -X POST` instead. |
| The incident's `model` field says `cached` | No Groq API key is set, or the call failed | This is expected without a key. See [Use a live language model](#use-a-live-language-model). |
