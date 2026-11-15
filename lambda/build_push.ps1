<#
Build and push the vfl-extract Lambda image (Spec_vfl-step10c D2). Plumbing.
Run from anywhere; needs Docker running and the vfl-build AWS profile with the scoped ECR policy.

  .\lambda\build_push.ps1 -PinBase      # once: print the digest of the Lambda base image to pin in the Dockerfile
  .\lambda\build_push.ps1               # build, smoke-start, push; prints the image URI with digest

Use the printed URI as:  terraform apply -var "extract_image_uri=<printed URI>"
#>
param(
    [string]$Profile = "vfl-build",
    [string]$Region = "us-east-1",
    [switch]$PinBase
)
$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if ($PinBase) {
    docker pull --platform linux/amd64 public.ecr.aws/lambda/python:3.12 | Out-Null
    $digest = docker inspect --format "{{index .RepoDigests 0}}" public.ecr.aws/lambda/python:3.12
    Write-Output "Pin this in lambda/extract/Dockerfile as ARG BASE_IMAGE=$digest"
    exit 0
}

$account = (aws sts get-caller-identity --profile $Profile --query Account --output text).Trim()
$registry = "$account.dkr.ecr.$Region.amazonaws.com"
$repo = "$registry/vfl-extract"
$tag = (git -C $repoRoot rev-parse --short HEAD).Trim()

docker build --platform linux/amd64 -f (Join-Path $repoRoot "lambda/extract/Dockerfile") -t "vfl-extract:$tag" $repoRoot
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

aws ecr get-login-password --profile $Profile --region $Region | docker login --username AWS --password-stdin $registry
if ($LASTEXITCODE -ne 0) { throw "ECR login failed" }

docker tag "vfl-extract:$tag" "${repo}:$tag"
docker push "${repo}:$tag"
if ($LASTEXITCODE -ne 0) { throw "docker push failed (tags are immutable: commit first so the tag is new)" }

$digest = (aws ecr describe-images --profile $Profile --region $Region --repository-name vfl-extract --image-ids imageTag=$tag --query "imageDetails[0].imageDigest" --output text).Trim()
Write-Output "extract_image_uri = $repo@$digest"
