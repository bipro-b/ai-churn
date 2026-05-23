# ECR = Elastic Container Registry: AWS's private Docker registry.
# Your CI builds the image and pushes it here; ECS pulls from here to run it.

resource "aws_ecr_repository" "app" {
  name                 = var.app_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true # auto-scan images for known CVEs on push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Lifecycle policy: keep the registry from growing forever (and costing money).
# Retain only the 10 most recent images.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
