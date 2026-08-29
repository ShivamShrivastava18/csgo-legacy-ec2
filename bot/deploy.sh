#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
[[ -f .env ]] || { echo "Copy .env.example to .env and fill it in first."; exit 1; }
set -a; source .env; set +a

PROFILE="${AWS_PROFILE:-personal}"
REGION="${AWS_REGION:-ap-south-1}"
AWS=(aws --profile "$PROFILE" --region "$REGION")
ACCOUNT=$("${AWS[@]}" sts get-caller-identity --query Account --output text)
FUNC=csgo-discord-bot
ROLE=csgo-discord-bot-role
RULE=csgo-hourly-reminder
INSTANCE_ARN="arn:aws:ec2:$REGION:$ACCOUNT:instance/$INSTANCE_ID"

build_zip() {
  rm -rf build function.zip
  mkdir -p build
  pip3 install pynacl -t build/ --platform manylinux2014_x86_64 \
    --only-binary=:all: --python-version 3.12 --quiet
  cp a2s.py discord_api.py server_control.py lambda_function.py build/
  (cd build && zip -qr ../function.zip .)
}

ensure_role() {
  if ! "${AWS[@]}" iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    "${AWS[@]}" iam create-role --role-name "$ROLE" --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
    }' >/dev/null
    sleep 10
  fi
  "${AWS[@]}" iam put-role-policy --role-name "$ROLE" --policy-name csgo-bot-policy \
    --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [
        {\"Effect\": \"Allow\", \"Action\": [\"ec2:StartInstances\", \"ec2:StopInstances\", \"ec2:CreateTags\"], \"Resource\": \"$INSTANCE_ARN\"},
        {\"Effect\": \"Allow\", \"Action\": \"ec2:DescribeInstances\", \"Resource\": \"*\"},
        {\"Effect\": \"Allow\", \"Action\": \"ssm:SendCommand\", \"Resource\": [\"$INSTANCE_ARN\", \"arn:aws:ssm:$REGION::document/AWS-RunShellScript\"]},
        {\"Effect\": \"Allow\", \"Action\": \"ssm:GetCommandInvocation\", \"Resource\": \"*\"},
        {\"Effect\": \"Allow\", \"Action\": \"lambda:InvokeFunction\", \"Resource\": \"arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNC\"},
        {\"Effect\": \"Allow\", \"Action\": [\"logs:CreateLogGroup\", \"logs:CreateLogStream\", \"logs:PutLogEvents\"], \"Resource\": \"*\"}
      ]
    }"
}

ENV_VARS=$(python3 -c 'import json, os; keys = ["DISCORD_PUBLIC_KEY", "DISCORD_BOT_TOKEN", "DISCORD_APP_ID", "DISCORD_CHANNEL_ID", "INSTANCE_ID", "SV_PASSWORD"]; print(json.dumps({"Variables": {k: os.environ.get(k, "") for k in keys}}))')

deploy_lambda() {
  if "${AWS[@]}" lambda get-function --function-name "$FUNC" >/dev/null 2>&1; then
    "${AWS[@]}" lambda update-function-code --function-name "$FUNC" \
      --zip-file fileb://function.zip >/dev/null
    "${AWS[@]}" lambda wait function-updated --function-name "$FUNC"
    "${AWS[@]}" lambda update-function-configuration --function-name "$FUNC" \
      --timeout 420 --memory-size 512 --environment "$ENV_VARS" >/dev/null
  else
    "${AWS[@]}" lambda create-function --function-name "$FUNC" \
      --runtime python3.12 --handler lambda_function.lambda_handler \
      --role "arn:aws:iam::$ACCOUNT:role/$ROLE" \
      --zip-file fileb://function.zip \
      --timeout 420 --memory-size 512 --environment "$ENV_VARS" >/dev/null
  fi
  "${AWS[@]}" lambda wait function-active --function-name "$FUNC"
}

ensure_url() {
  if ! "${AWS[@]}" lambda get-function-url-config --function-name "$FUNC" >/dev/null 2>&1; then
    "${AWS[@]}" lambda create-function-url-config --function-name "$FUNC" \
      --auth-type NONE >/dev/null
    "${AWS[@]}" lambda add-permission --function-name "$FUNC" \
      --statement-id FunctionURLAllowPublicAccess \
      --action lambda:InvokeFunctionUrl --principal "*" \
      --function-url-auth-type NONE >/dev/null
  fi
}

ensure_schedule() {
  "${AWS[@]}" events put-rule --name "$RULE" --schedule-expression "rate(1 hour)" >/dev/null
  "${AWS[@]}" events put-targets --rule "$RULE" --targets \
    "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNC\",\"Input\":\"{\\\"source\\\":\\\"hourly-check\\\"}\"}]" >/dev/null
  "${AWS[@]}" lambda add-permission --function-name "$FUNC" \
    --statement-id EventBridgeHourly --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$ACCOUNT:rule/$RULE" >/dev/null 2>&1 || true
}

onboard_instance() {
  local IROLE=csgo-server-ssm
  if ! "${AWS[@]}" iam get-role --role-name "$IROLE" >/dev/null 2>&1; then
    "${AWS[@]}" iam create-role --role-name "$IROLE" --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
    }' >/dev/null
    "${AWS[@]}" iam attach-role-policy --role-name "$IROLE" \
      --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
    "${AWS[@]}" iam create-instance-profile --instance-profile-name "$IROLE" >/dev/null
    "${AWS[@]}" iam add-role-to-instance-profile --instance-profile-name "$IROLE" --role-name "$IROLE"
    sleep 10
  fi
  if ! "${AWS[@]}" ec2 describe-iam-instance-profile-associations \
      --filters "Name=instance-id,Values=$INSTANCE_ID" \
      --query 'IamInstanceProfileAssociations[0]' --output text | grep -q "$IROLE"; then
    "${AWS[@]}" ec2 associate-iam-instance-profile --instance-id "$INSTANCE_ID" \
      --iam-instance-profile "Name=$IROLE" >/dev/null
  fi
  "${AWS[@]}" ec2 modify-instance-metadata-options --instance-id "$INSTANCE_ID" \
    --instance-metadata-tags enabled >/dev/null
  echo "Instance role and metadata tags configured."
  echo "Start the instance, wait for SSM to register, then run: $0 --install-boot-script"
}

install_boot_script() {
  local B64
  B64=$(base64 < server/csgo-boot.sh | tr -d '\n')
  "${AWS[@]}" ssm send-command --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --parameters "commands=[
      \"echo $B64 | base64 -d > /home/csgoserver/csgo-boot.sh\",
      \"chmod +x /home/csgoserver/csgo-boot.sh\",
      \"chown csgoserver:csgoserver /home/csgoserver/csgo-boot.sh\",
      \"sudo -u csgoserver bash -c 'crontab -l 2>/dev/null | grep -v csgo-boot; echo \\\"@reboot /home/csgoserver/csgo-boot.sh >> /home/csgoserver/boot.log 2>&1\\\"' | sudo -u csgoserver crontab -\"
    ]" \
    --query 'Command.CommandId' --output text
}

case "${1:-deploy}" in
  --onboard-instance) onboard_instance ;;
  --install-boot-script) install_boot_script ;;
  deploy)
    build_zip
    ensure_role
    deploy_lambda
    ensure_url
    ensure_schedule
    "${AWS[@]}" lambda get-function-url-config --function-name "$FUNC" \
      --query FunctionUrl --output text
    ;;
  *) echo "Usage: $0 [deploy | --onboard-instance | --install-boot-script]"; exit 1 ;;
esac
