$ErrorActionPreference = "Stop"

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$image = "vol-forecast-lab-airflow:3.4.0"

$dagTests = Get-ChildItem -LiteralPath (Join-Path $repo "tests") -Filter "test_dag*.py" -File -ErrorAction SilentlyContinue
if (-not $dagTests) {
    Write-Output "No tests/test_dag*.py files found."
    exit 0
}

docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker image '$image' is missing. Build it from airflow/Dockerfile first."
}

$dockerArgs = @(
    "run", "--rm",
    "--volume", "${repo}:/opt/vfl:ro",
    "--workdir", "/opt/vfl",
    "--env", "AIRFLOW_HOME=/tmp/airflow",
    "--env", "AIRFLOW__CORE__LOAD_EXAMPLES=False",
    "--env", "AIRFLOW__CORE__DAGS_FOLDER=/opt/vfl/dags",
    "--env", "VFL_PYTHON=/opt/vfl-venv/bin/python",
    "--env", "VFL_REPO=/opt/vfl",
    $image,
    "bash", "-lc",
    'mkdir -p "$AIRFLOW_HOME" && airflow db migrate && python -m pip install pytest==9.2.0 && python -m pytest -q -p no:cacheprovider -m airflow tests/test_dag*.py'
)

& docker @dockerArgs
exit $LASTEXITCODE
