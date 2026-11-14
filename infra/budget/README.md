# Cost guardrail (apply first, never destroy)

Account-wide AWS Budget of USD 10 per month with email alerts at 50% and 80% of actual spend and
100% of forecast spend. Apply this root **before** `infra/terraform`. It has its own local state and
`prevent_destroy`, so the evidence-run `terraform destroy` never removes it.

The alert address is not stored in the repository. Put it in `infra/budget/terraform.tfvars`
(git-ignored), e.g. `alert_email = "you@example.com"`, or set `$env:TF_VAR_alert_email`.

```powershell
$env:AWS_PROFILE = "vfl-build"
terraform -chdir=infra/budget init
terraform -chdir=infra/budget validate
terraform -chdir=infra/budget plan -out tfplan
terraform -chdir=infra/budget apply tfplan
```

The profile needs `budgets:ViewBudget` and `budgets:ModifyBudget`. AWS Budgets data refreshes up to
three times a day, so an alert is a backstop, not a real-time stop.
