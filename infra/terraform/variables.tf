variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "owner" {
  description = "Owner tag for all resources (your name/identifier)."
  type        = string
  default     = "learner"
}

variable "app_name" {
  description = "Base name for resources."
  type        = string
  default     = "churn-api"
}

variable "container_port" {
  description = "Port the container listens on."
  type        = number
  default     = 8000
}

variable "desired_count" {
  description = "Number of Fargate tasks to run."
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 1024
}

variable "image_tag" {
  description = "Container image tag to deploy (usually the git SHA)."
  type        = string
  default     = "latest"
}
