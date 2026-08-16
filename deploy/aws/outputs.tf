output "app_url" {
  description = "Application URL"
  value       = var.acm_certificate_arn == "" ? "http://${aws_lb.main.dns_name}" : "https://${aws_lb.main.dns_name}"
}

output "alb_dns" {
  value = aws_lb.main.dns_name
}

output "s3_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
