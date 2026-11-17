# Step 13 evidence

Date: 2026-11-17

## Tool versions

- Docker: client 29.9.2 / server 29.9.2
- kind: v0.33.0 (go1.26.7, windows/amd64)
- kubectl: v1.36.1 (client); Kubernetes node image: kindest/node:v1.37.0
- Airflow image: `vol-forecast-lab-airflow:3.4.0`, present locally; image ID `sha256:752eb4947bd5272de5ff71d1f3c759a80242b4e29a4fa655f22d26ce49990b08`

## Commands and results

### Manifest validation

Command:

```powershell
docker run --rm -v "${PWD}/k8s:/k8s:ro" ghcr.io/yannh/kubeconform:latest -strict -summary -kubernetes-version 1.37.0 -ignore-filename-pattern kind-config.yaml /k8s
```

Output:

```text
Summary: 4 resources found in 3 files - Valid: 4, Invalid: 0, Errors: 0, Skipped: 0
```

### Cluster startup and health

Command: `./scripts/kind_up.ps1`

The script created the `vfl` kind cluster, built and loaded `vol-forecast-lab:local`, and applied the four manifests. It then stopped at `kubectl rollout status deploy --all` because kubectl 1.36.1 returned `error: unknown flag: --all`. The deployment was checked directly:

```powershell
kubectl rollout status deploy/vfl-api --timeout=120s
curl.exe --fail --show-error --silent http://127.0.0.1:30080/health
```

Output:

```text
deployment "vfl-api" successfully rolled out
{"status":"ok","version":"0.1.0"}
```

### Resources and deployment details

Commands:

```powershell
kubectl get deploy,svc,cronjob,configmap -o wide
kubectl describe deploy vfl-api
```

Trimmed final output:

```text
deployment.apps/vfl-api   1/1   1   1   api   vol-forecast-lab:local
service/vfl-api           NodePort   10.96.196.62   <none>   8000:30080/TCP
cronjob.batch/vfl-drift    0 6 1 * *   False   0   <none>   drift   vol-forecast-lab:local
configmap/vfl-config      5
```

```text
Replicas: 1 desired | 1 updated | 1 total | 1 available | 0 unavailable
Limits:   cpu 500m, memory 512Mi
Requests: cpu 100m, memory 256Mi
Liveness:  http-get :8000/health, delay=10s, timeout=1s, period=20s
Readiness: http-get :8000/health, delay=3s, timeout=1s, period=10s
Environment Variables from: vfl-config ConfigMap (Optional: false)
```

`kubectl describe deploy` does not print container `securityContext`. Checked instead with:

```powershell
kubectl get deploy vfl-api -o jsonpath="{.spec.template.spec.containers[0].securityContext}"
kubectl exec deploy/vfl-api -- id
```

Output (17 Nov 2026):

```text
{"allowPrivilegeEscalation":false,"runAsNonRoot":true,"runAsUser":10001}
uid=10001(appuser) gid=10001(appuser) groups=10001(appuser)
```

### Drift job

Commands:

```powershell
kubectl create job --from=cronjob/vfl-drift drift-test
kubectl wait --for=condition=complete job/drift-test --timeout=180s
kubectl logs job/drift-test
```

Output:

```text
job.batch/drift-test created
job.batch/drift-test condition met
```

First 40 log lines (complete log):

```json
{
  "train_end": "2019-12-02",
  "snapshot_id": "20261112T071104Z",
  "psi_window": 126,
  "qlike_window": 63,
  "thresholds": {
    "psi": 0.25,
    "qlike_ratio": 1.1,
    "qlike_ratio_months": 3
  },
  "first_month_covered": "2020-01",
  "last_month_covered": "2026-11",
  "n_months_flagged_per_input": {
    "rv_d": 40,
    "rv_w": 64,
    "rv_m": 78,
    "iv_ps": 78
  },
  "months_degraded": [
    "2020-06",
    "2020-07",
    "2020-08",
    "2020-09",
    "2021-03",
    "2021-07",
    "2021-08",
    "2021-09",
    "2021-10",
    "2024-10",
    "2026-05"
  ],
  "latest_month_status": {
    "psi_month": "2026-11",
    "qlike_month": "2026-10",
    "inputs": {
      "rv_d": false,
      "rv_w": false,
      "rv_m": true,
      "iv_ps": true
    },
    "degraded": false
  },
  "code_commit": "unknown"
}
```

### DAG test evidence

run_dag_tests.ps1: 4 passed (c849f27)

## Warnings and notes

- `kind_up.ps1` did not complete its own rollout command because `kubectl rollout status deploy --all` is unsupported by the installed kubectl; the named deployment rollout subsequently succeeded.
- The first `/health` request during startup returned an empty reply. The later health check returned HTTP success with `{"status":"ok","version":"0.1.0"}`.
- Kubernetes events recorded transient warnings during startup: the node briefly reported NotReady, one initial readiness probe was refused before the API started, and the manually-created `drift-test` job was reported by the CronJob controller as unexpected. The API deployment and drift job reached healthy/completed status.
- No secrets or `.env` values are included.
