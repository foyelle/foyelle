// The canvas CSP has no 'unsafe-eval'; tell zod (used by the ext-apps
// AppBridge for message validation) not to probe for or use `new Function`.
import { config } from "zod/v4/core";

config({ jitless: true });
