output "budget_name" {
  description = "Name of the account cost budget."
  value       = aws_budgets_budget.vfl_monthly.name
}
