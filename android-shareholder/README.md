# Shareholder app (Android)

A phone app for shareholders: this year's profit, the cash position, where the
money came from and went, the owners' equity, and the same chatbot as the web
app — ask it anything and it answers from the live books.

## How it connects

The app **never connects to PostgreSQL**. It signs in over HTTPS to your own
accounting server and reads through `/api/mobile/*`:

```
phone ──HTTPS──> accounting-app on Render ──> PostgreSQL
      (bearer token)        (database password stays here)
```

An APK can be unzipped and decompiled in minutes. A database password inside
one is a password given away — with full read *and write* access to the live
books, bypassing every user permission and the audit trail. Hence the API.

What the app stores on the phone: the server address and a sign-in token that
expires after 30 days. The password is never written to disk. "Sign out"
deletes the token on the server, so a lost phone can be cut off from the web
app's user screen.

## Endpoints it uses

| Endpoint | Purpose |
| --- | --- |
| `POST /api/mobile/login` | username + password → bearer token |
| `POST /api/mobile/logout` | revoke this device's token |
| `GET /api/mobile/dashboard` | profit, cash, receivables, payables, stock, sales trend, top customers |
| `GET /api/mobile/shareholder` | full P&L breakdown, equity, comparison |
| `POST /api/mobile/chat` | the chatbot |
| `GET /api/mobile/ping` | wakes a sleeping free instance |

All of them are read-only. There is no endpoint here that writes a voucher, a
master record or a setting, so nothing a shareholder does in this app can
change the books.

## Try it before building an APK

The whole app is plain HTML/CSS/JS in `www/`. Serve that folder and open it in
a browser:

```bash
cd android-shareholder/www && python -m http.server 8100
```

Then browse to `http://localhost:8100`, enter your server address
(`https://accounting-app-stvp.onrender.com`) and sign in. What you see is
exactly what the phone shows.

## Building the APK

Needs [Node.js](https://nodejs.org) and
[Android Studio](https://developer.android.com/studio) (for the Android SDK
and a JDK).

```bash
cd android-shareholder
npm install
npx cap add android      # first time only - creates the android/ project
npx cap sync android
npx cap open android     # opens Android Studio
```

In Android Studio: **Build → Build Bundle(s)/APK(s) → Build APK(s)**. The debug
APK lands in `android/app/build/outputs/apk/debug/`. Send that file to a
shareholder; they enable "install from unknown sources" once and tap it.

For the Play Store you need a signed release build — Android Studio's
**Build → Generate Signed Bundle/APK** walks through creating a keystore. Keep
that keystore safe; losing it means you can never update the app.

Rebuild after any change to `www/`:

```bash
npx cap sync android
```

## Giving a shareholder access

1. In the web app: **Setup → User Management**, create a user for them.
2. Give them the **Reports** permission (they need nothing else — the mobile
   endpoints do not check menu permissions, but keeping the web account
   minimal is good practice).
3. Assign them to the company.
4. Send them the APK and the server address.

Tokens last 30 days, after which the app asks them to sign in again.

## Notes

- **First load is slow.** Render's free instance sleeps after inactivity, so
  the first sign-in can take ~50 seconds. The app says so while it waits.
  A paid instance removes this.
- **Figures are management figures**, live from the accounting system for the
  current financial year — not audited accounts. The app says this on the
  shareholding screen.
- **Equity signs are flipped server-side.** Capital and reserves are credit
  balances, stored negative; shareholders read their stake as a positive
  number, so `/api/mobile/shareholder` returns it that way.
