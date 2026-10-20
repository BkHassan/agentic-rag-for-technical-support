# File Upload Failures

## Symptoms
Users cannot upload files. The UI may hang, return 413, 415, or a generic “upload failed” message.

## Common causes
- File exceeds application or reverse-proxy max body size limits
- Unsupported MIME type or file extension blocked by validation
- Temporary storage disk full on the application server
- Object storage credentials or bucket permissions incorrect
- Network interruption during large multipart uploads
- Antivirus or content-scanning gateway rejecting the payload

## Troubleshooting steps
1. Compare the file size to app, proxy (`client_max_body_size`), and storage limits.
2. Confirm allowed content types and extensions in server validation rules.
3. Check free disk space for temporary upload directories.
4. Verify cloud storage IAM permissions for put/object create.
5. Retry with a small known-good file to separate size vs auth vs network issues.
6. Review gateway/WAF logs if uploads fail only outside the internal network.

## Related notes
A 413 Payload Too Large almost always means a proxy or app size limit, not a client bug.
