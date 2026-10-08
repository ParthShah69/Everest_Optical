# AI assistant setup

After deploying this change, run the new Alembic migration against the existing database:

```powershell
cd backend
python -m flask --app app db upgrade
```

Sign in as an admin and open **AI Providers** in the sidebar. Add a provider, a current model ID with tool calling support, its API key, and a priority. Supported providers are Gemini, Groq, OpenRouter, and Cerebras. Lower priority numbers are tried first. When a provider returns a rate limit or temporary error, the assistant tries the next enabled entry. Each attempt is bounded; the assistant does not loop through keys indefinitely.

Keys are encrypted in the database and are never displayed again. The deployment must keep its existing `SECRET_KEY` private and stable: changing it makes previously saved keys unreadable. No provider API key needs to be added to Vercel environment settings. Admins can disable or remove a key on the same page.

The assistant stages every create, update, payment, stock change, or delete tool call. The operator must click **Approve** before it runs. A pending action expires after 15 minutes and can only be approved once by the same signed-in user in the same chat. Deleting a customer with related orders also requires typing the confirmation phrase in a modal. The full database reset is available only through the separate Manage Users modal, which requires typed confirmation and preserves the current admin's sign-in credentials.

Conversation messages remain in the database. Older turns are folded into a short rolling context note so the assistant can continue long conversations with bounded provider usage. After a page switch, the widget starts collapsed and recovers pending work from chat history. The browser does not automatically resend an unfinished mutation.

Provider models and free allowances change over time; choose a model allowed by the account and that supports tool calling. Official endpoint references: [Gemini](https://ai.google.dev/gemini-api/docs/openai), [OpenRouter](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request), [Cerebras](https://inference-docs.cerebras.ai/resources/glm-47-migration).
