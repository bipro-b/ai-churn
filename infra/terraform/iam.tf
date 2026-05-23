# IAM roles. Least-privilege is a core production principle.
#
# Two distinct roles, on purpose:
# - execution_role: used by the ECS AGENT to pull the image from ECR and write
#   logs to CloudWatch. This is infrastructure plumbing.
# - task_role: the identity YOUR CODE runs as. If the app needs to read a model
#   from S3 or fetch a secret, you grant THAT permission here — narrowly.
#   Separating these means your app never gets ECR/logging powers it shouldn't have.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.app_name}-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${var.app_name}-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# Example: if you later store the model in S3, you'd attach a narrow policy here
# granting s3:GetObject on ONLY that bucket/prefix. Left commented as a template.
#
# resource "aws_iam_role_policy" "task_s3" {
#   name = "read-model-bucket"
#   role = aws_iam_role.task.id
#   policy = jsonencode({
#     Version = "2012-10-17"
#     Statement = [{
#       Effect   = "Allow"
#       Action   = ["s3:GetObject"]
#       Resource = "arn:aws:s3:::your-model-bucket/models/*"
#     }]
#   })
# }
