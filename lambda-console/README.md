# Lambda Console Copy/Paste Versions

These files are standalone Python handlers intended for the AWS Lambda console.

## Files

- `get_call_status_lambda.py`
- `get_call_log_detail_lambda.py`
- `post_call_correction_lambda.py`
- `update_auto_call_lambda.py`
- `start_outbound_call_lambda.py`

## Suggested Lambda names

- `GetCallStatus`
- `GetCallLogDetail`
- `PostCallCorrection`
- `UpdateAutoCall`
- `StartOutboundCall`

## Runtime

- Python 3.12

## Required environment variables

### GetCallStatus

- `TARGETS_TABLE`
- `SESSIONS_TABLE`
- `CALL_DATE_INDEX=CallDateIndex`
- `AUTO_CALL_STATUS_INDEX=AutoCallStatusIndex`
- `ADMIN_GROUP=admin`

### GetCallLogDetail

- `TARGETS_TABLE`
- `SESSIONS_TABLE`
- `RECORDINGS_BUCKET`
- `ANALYSIS_BUCKET`
- `PRESIGNED_URL_EXPIRES=3600`
- `ADMIN_GROUP=admin`

### UpdateAutoCall

- `TARGETS_TABLE`
- `ADMIN_GROUP=admin`

### PostCallCorrection

- `CALL_CORRECTIONS_TABLE=carecall-call-corrections-dev`
- `APP_TIMEZONE=Asia/Seoul`

### StartOutboundCall

- `CONNECT_INSTANCE_ID`
- `CONTACT_FLOW_ID`
- `SOURCE_PHONE_NUMBER`
- `DEFAULT_DESTINATION_PHONE_NUMBER=01030890260`

## Handler

If you paste into the default Lambda file, use:

- `lambda_function.lambda_handler`
