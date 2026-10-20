# Multipart Uploads and Object Storage

## Symptoms
Large file uploads fail near the end, leave incomplete multipart uploads, or succeed in the UI but the object is missing.

## Common causes
- Multipart upload aborted after part timeout
- Incomplete upload not completed/committed
- Lifecycle rules deleting incomplete multipart uploads aggressively
- Clock skew breaking signed URL validity
- Wrong region or endpoint for the storage client

## Troubleshooting steps
1. Inspect incomplete multipart upload lists in the bucket.
2. Increase part size / timeout for unreliable networks.
3. Confirm signed URL expiry covers the full upload duration.
4. Verify the client uses the correct region and endpoint.
5. Ensure the complete-multipart API call runs after the final part.

## Related notes
Always clean up abandoned multipart uploads to avoid storage cost surprises.
