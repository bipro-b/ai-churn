output "ecr_repository_url" {
  description = "Push your image here."
  value       = aws_ecr_repository.app.repository_url
}

output "api_url" {
  description = "Public URL of the deployed API."
  value       = "http://${aws_lb.app.dns_name}"
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "service_name" {
  value = aws_ecs_service.app.name
}
