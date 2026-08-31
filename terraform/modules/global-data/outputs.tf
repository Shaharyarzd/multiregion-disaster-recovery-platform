output "table_name" { value = aws_dynamodb_table.transactions.name }
output "table_arn" { value = aws_dynamodb_table.transactions.arn }
output "primary_bucket" { value = aws_s3_bucket.primary.id }
output "secondary_bucket" { value = aws_s3_bucket.secondary.id }
output "primary_kms_key_arn" { value = aws_kms_key.primary.arn }
output "secondary_kms_key_arn" { value = aws_kms_key.secondary.arn }

