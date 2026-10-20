#!/bin/bash
# DEPRECATED — this script is not on the live build/deploy path.
#
# The UI is built and deployed via the Makefile (which CI uses):
#   make build-ui           # generates source/ui/.env from CDK outputs, then `npm run build`
#   make deploy-ui-stack    # cdk deploy CMUIStack (pushes dist/ to S3 + CloudFront)
#
# Its former helper, build-ui.sh, was removed (it pointed at the pre-restructure
# `ui/` dir and the retired App.auth.tsx swap). Use the make targets above.
echo "deploy-ui.sh is deprecated. Use: make build-ui && make deploy-ui-stack" >&2
exit 1
