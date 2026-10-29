# Sign in with Google (Gmail and Google Calendar)

Gmail mail works with an app password, but Google Calendar does not: Google's CalDAV only accepts
Google's own sign-in (OAuth 2.0). Google allows that sign-in for a desktop app only in a real
browser (embedded web views are refused, and the device-code sign-in doesn't cover Gmail or
Calendar), so Foyelle has one browser for this single job:

1. In the Accounts card, the person chooses Gmail → *Sign in with Google*.
2. Firefox opens full screen over the canvas, on Google's sign-in page.
3. The person signs in (with their usual 2-Step Verification) and allows Foyelle.
4. Google sends Firefox back to `http://127.0.0.1:<port>/` on the same computer, with a one-time
   code. The canvas, listening there, closes Firefox and hands the code to the helper that started
   the sign-in, which exchanges it with Google, directly, for tokens.

Mail and calendar are separate approvals in the same browser session, so the password is typed once
and each helper holds only its own access (`https://mail.google.com/` for mail,
`https://www.googleapis.com/auth/calendar` for the calendar).

The pieces: `nixos/modules/signin.nix` (Firefox, its policies and its proxy),
`canvas/src/main/signin.ts` (the loopback listener and the browser),
`connectors/src/foyelle_connectors/sdk/google.py` (PKCE, the code exchange, token renewal).

## How the browser is kept to that job

- **Its own egress proxy** (`127.0.1.101`), which reaches Google's sign-in hosts on port 443 and
  nothing else (`foyelle.signin.googleHosts`). The kiosk itself may reach only loopback and that proxy.
- **Locked policies:** every page is blocked except Google's sign-in and the loopback answer; no
  add-ons, `about:config`, developer tools, telemetry, studies, saved passwords or form history; the
  proxy and DNS settings are locked.
- **A throwaway profile:** made when the Accounts card starts a sign-in, deleted when it's done, so
  no Google cookie stays on the computer.
- **The code is only half of it.** The helper keeps the PKCE verifier and a random `state`; the code
  alone can't be exchanged, and a code for a different sign-in is refused. The code is a secret
  argument (`secret_args`), so it is never hashed into the audit log.

Size: Firefox adds about 460 MB to the image.

## What sees what

| | Sees |
|---|---|
| Google | That this Google account uses "Foyelle" (listed under the account's third-party access), and the API calls Foyelle makes, as it already sees for any mail client. |
| The Foyelle Google project | Counts of sign-ins and API calls in Google's console. No mail or calendar content. |
| The computer | The refresh token, in the helper's own 0700 state directory, like an app password. Nothing from the browser is kept. |

No Foyelle server is involved.

Trade-offs to know:

- A desktop client's "secret" ships in Foyelle's image. Google treats desktop clients as public, so
  that's expected; the PKCE verifier is what protects a sign-in.
- Google can suspend the project; every Foyelle signed in with it would lose Google access at once.
- Until Google reviews the app, people see an "unverified app" warning, and at most 100 people can
  ever sign in with the project (a lifetime limit). Full Gmail access (`https://mail.google.com/`) is
  a restricted scope: after review it also needs a yearly security assessment by an approved lab.

## 1. Google Cloud project (in the Google Cloud console, signed in with the account that owns Foyelle)

1. Create a project named **Foyelle**.
2. **APIs & Services → Library:** enable the **CalDAV API** (Google Calendar's CalDAV). Gmail over
   IMAP needs no API.
3. **Google Auth Platform → Branding:** app name **Foyelle**, a support email, and (optional for now)
   logo, homepage and privacy policy links.
4. **Audience:** user type **External**. While trying it out, either add your own Google account
   under *Test users* (sign-ins then expire after 7 days), or choose **Publish app** (the
   "unverified app" warning stays, the 100-user limit applies, and sign-ins don't expire).
5. **Data Access:** add the scopes `openid`, `.../auth/userinfo.email`,
   `https://mail.google.com/` and `https://www.googleapis.com/auth/calendar`.
6. **Clients → Create client:** type **Desktop app**, name **Foyelle**. A desktop client needs no
   redirect URIs: Google accepts any `http://127.0.0.1:<port>/`. Download the client's JSON.

## 2. Foyelle

In the machine's configuration (`configuration.nix` in its config repository), from the JSON:

```nix
foyelle.google = {
  clientId = "<id>.apps.googleusercontent.com";
  clientSecret = "<the desktop client's secret>";
};
```

After the change is applied, **Gmail** in the Accounts card offers *Sign in with Google* for mail and
calendar (app passwords stay available for mail). Without `clientId`, neither Firefox nor its proxy
is installed.

## Trying it in development

`npm run dev` in `canvas/` has no browser: the canvas answers the sign-in itself with a made-up code,
and the tools host plays Google's token endpoint, so the whole card flow runs without Google.
