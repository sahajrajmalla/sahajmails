/* Thin fetch wrapper.
 *
 * Every state-changing call carries the X-SahajMails header. The server rejects
 * mutations without it, which is what stops a random page in another tab from
 * driving this API — a plain cross-site form post cannot set a custom header.
 */

const CSRF_HEADER = "X-SahajMails";

export class ApiError extends Error {
  constructor(message, { hint = "", status = 0 } = {}) {
    super(message);
    this.name = "ApiError";
    this.hint = hint;
    this.status = status;
  }
}

async function request(method, path, body, { raw = false } = {}) {
  const options = {
    method,
    headers: { [CSRF_HEADER]: "1" },
    credentials: "same-origin",
  };

  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch (cause) {
    throw new ApiError("Cannot reach the SahajMails server.", {
      hint: "Is it still running in your terminal?",
    });
  }

  if (raw) return response;

  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const message =
      payload?.error ||
      payload?.detail ||
      (Array.isArray(payload?.detail) ? payload.detail[0]?.msg : null) ||
      `Request failed (${response.status})`;
    throw new ApiError(String(message), {
      hint: payload?.hint || "",
      status: response.status,
    });
  }
  return payload;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  del: (path) => request("DELETE", path),

  upload(path, file) {
    const form = new FormData();
    form.append("file", file);
    return request("POST", path, form);
  },

  /** Subscribe to a job's server-sent events. Returns a close function. */
  events(jobId, handlers) {
    const source = new EventSource(`/api/jobs/${jobId}/events`, {
      withCredentials: true,
    });
    for (const [name, handler] of Object.entries(handlers)) {
      source.addEventListener(name, (event) => {
        let data = {};
        try {
          data = JSON.parse(event.data);
        } catch {
          /* keepalive comment frames have no payload */
        }
        handler(data);
        if (name === "done") source.close();
      });
    }
    source.onerror = () => {
      // EventSource retries on its own; only a finished job closes it.
      if (source.readyState === EventSource.CLOSED) handlers.closed?.();
    };
    return () => source.close();
  },
};
