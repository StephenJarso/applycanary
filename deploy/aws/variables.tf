variable "region" {
  description = "AWS region (keep in the same region as the CockroachDB serverless cluster for low latency)"
  type        = string
  default     = "us-east-1"
}

variable "app_name" {
  description = "Name prefix for all AWS resources"
  type        = string
  default     = "applycanary"
}

variable "image" {
  description = "Container image (ECR or public). Build with: docker build -t applycanary:latest ."
  type        = string
  default     = "applycanary:latest"
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "desired_count" {
  description = "Running task count. Keep 1: the scheduler runs inside the app process."
  type        = number
  default     = 1
}

variable "db_url" {
  description = "CockroachDB connection string (from ccloud provision.sh). Stored in SSM, never in git."
  type        = string
  sensitive   = true
}

variable "secret_key" {
  description = "Random session-signing secret (python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  type        = string
  sensitive   = true
}

variable "llm_api_key" {
  description = "Optional: Gemini/OpenRouter/Groq key. Bedrock needs no key — IAM handles it."
  type        = string
  default     = ""
  sensitive   = true
}

variable "extra_env" {
  description = "Additional environment variables (e.g. SMTP_*, GITHUB_USERNAME, ENABLE_AUTO_SUBMIT)"
  type        = map(string)
  default     = {}
}

variable "acm_certificate_arn" {
  description = "ACM certificate for HTTPS. Leave empty to serve plain HTTP on :80 (demo only)."
  type        = string
  default     = ""
}

variable "allowed_cidrs" {
  description = "CIDRs allowed to reach the ALB (0.0.0.0/0 for a public demo)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
