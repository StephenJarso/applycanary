import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "./api";
import { useAuth } from "./context/AuthContext";
import { ErrorBox } from "./components";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export function FeedbackModal({ isOpen, onClose }: Props) {
  const { user } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [category, setCategory] = useState<"feedback" | "bug" | "feature" | "contact">("feedback");
  const [message, setMessage] = useState("");
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      api.submitFeedback({
        name: name.trim() || (user?.email ? user.email.split("@")[0] : ""),
        email: email.trim() || user?.email || "",
        category,
        message: message.trim(),
      }),
    onSuccess: () => {
      setSuccess(true);
      setTimeout(() => {
        setSuccess(false);
        setMessage("");
        onClose();
      }, 2000);
    },
  });

  if (!isOpen) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-title"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 480,
          width: "90%",
          margin: "auto",
          background: "var(--ac-surface, #fff)",
          boxShadow: "0 10px 25px rgba(0,0,0,0.2)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 id="feedback-title" className="card-title" style={{ margin: 0 }}>
            Feedback &amp; Contact
          </h3>
          <button
            type="button"
            className="btn-ghost"
            onClick={onClose}
            aria-label="Close"
            style={{ padding: 4 }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>close</span>
          </button>
        </div>

        <p className="cell-dim" style={{ fontSize: 13, marginBottom: 16 }}>
          Have a question, suggestion, or encountered a problem? Send a direct note to the administrator.
        </p>

        {success ? (
          <div className="banner banner-ok" role="status">
            Thank you! Your feedback has been sent to the administrator.
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (message.trim()) mutation.mutate();
            }}
          >
            <div className="field">
              <label htmlFor="fb-cat">Topic</label>
              <select
                id="fb-cat"
                value={category}
                onChange={(e) => setCategory(e.target.value as any)}
                style={{ padding: "8px 10px", borderRadius: "var(--radius)", border: "1px solid var(--border)" }}
              >
                <option value="feedback">General feedback</option>
                <option value="bug">Bug report</option>
                <option value="feature">Feature suggestion</option>
                <option value="contact">Contact admin</option>
              </select>
            </div>

            {!user && (
              <div className="grid2" style={{ gap: 10 }}>
                <div className="field">
                  <label htmlFor="fb-name">Your name (optional)</label>
                  <input
                    id="fb-name"
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Guest"
                  />
                </div>
                <div className="field">
                  <label htmlFor="fb-email">Your email (optional)</label>
                  <input
                    id="fb-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                  />
                </div>
              </div>
            )}

            <div className="field">
              <label htmlFor="fb-msg">Message</label>
              <textarea
                id="fb-msg"
                rows={4}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What's on your mind? Tell us what you love or what needs fixing..."
                required
                style={{ width: "100%", fontFamily: "inherit", padding: 8 }}
              />
            </div>

            {mutation.isError && <ErrorBox error={mutation.error} />}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              <button type="button" className="btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" disabled={mutation.isPending || !message.trim()}>
                {mutation.isPending ? <span className="spinner" /> : "Send message"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
