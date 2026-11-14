# Local Terraform state and AWS evidence run

The configuration uses the local AWS CLI profile `vfl-build` in `us-east-1`. Terraform state
stays in this directory; there is no remote backend. The bucket, Lambda, Glue catalog and Athena
workgroup can incur AWS charges while they exist. Athena limits each query to 100 MiB scanned.

Apply the cost guardrail in `infra/budget/` first (see its README); it is never destroyed.

The Glue table uses partition projection (years 2010–2030, doc_type statement|minutes). The Lambda
must write `clean/year=YYYY/doc_type=<statement|minutes>/*.parquet`; Athena needs no partition repair.

Run from the repository root in PowerShell:

```powershell
$env:AWS_PROFILE = "vfl-build"
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform plan -out tfplan
terraform -chdir=infra/terraform apply tfplan
```

After saving the evidence for the run, destroy the resources:

```powershell
$env:AWS_PROFILE = "vfl-build"
terraform -chdir=infra/terraform destroy
```

Run `terraform destroy` after the evidence run. `force_destroy` is enabled for the S3 bucket and
Athena workgroup so the evidence bucket can be removed even when it contains objects. Verify the
profile and region before applying; do not commit local state, plan files or generated ZIP files.

The default Lambda layer is the AWS SDK for pandas 3.16.1 managed layer for Python 3.12 x86_64 in
us-east-1, layer version 27. Its ARN includes the AWS publisher account required by the managed
layer address; no user AWS account ID is configured in this repository.
