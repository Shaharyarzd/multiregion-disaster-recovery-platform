# Terraform state and ownership boundaries

Apply order is `bootstrap` (one-time identity), `global` (data plane), then either regional
stack independently. Each stack intentionally has its own backend/state and blast radius.
Outputs from `global` are passed to regional stacks through an owner-controlled pipeline;
the modules do not read another state file directly. Example backend blocks are omitted so
no bucket, key, account, or workspace is implied.

No command in this repository automatically applies Terraform. The global DynamoDB table
uses `prevent_destroy`; point-in-time restore creates a new isolated table outside Terraform
before any reconciliation decision. Promotion and failback use the separately protected
recovery role/environment.

