# Networking.
#
# Senior note: for a learning project we use the account's DEFAULT VPC to keep
# things simple and free. In real production you'd define your own VPC with
# private subnets for the tasks and only the load balancer in public subnets.
# The runbook explains this tradeoff. The security-group design below is already
# production-correct: the ALB is the only thing exposed to the internet, and the
# tasks ONLY accept traffic from the ALB.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Security group for the Application Load Balancer: open to the world on 80.
resource "aws_security_group" "alb" {
  name        = "${var.app_name}-alb-sg"
  description = "Allow inbound HTTP to the ALB"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Security group for the Fargate tasks: ONLY accept traffic from the ALB SG.
# This is the key production pattern — tasks are never directly reachable.
resource "aws_security_group" "task" {
  name        = "${var.app_name}-task-sg"
  description = "Allow inbound from ALB only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "App port from ALB only"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
