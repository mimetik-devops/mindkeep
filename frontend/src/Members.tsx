import { useEffect, useState } from "react";

import {
  createInvite,
  invites,
  type Invite,
  type Member,
  members,
  me,
  removeMember,
  revokeInvite,
  type Role,
  setRole,
  type Team,
} from "./api";
import { Copy } from "./icons";

const ROLES: Role[] = ["owner", "admin", "member"];

/**
 * Who is in the team, and how to get someone in: the Settings card.
 *
 * Membership is Mindstash's own (see teams.py), so nothing here goes near the identity
 * provider. An invite is a link — make one, copy it, send it however you like; whoever
 * opens it joins as themselves. What each person may do here is what the server lets
 * them do, and the server says so when it refuses.
 */
export function Members({ team }: { team: Team }) {
  const [who, setWho] = useState<Member[]>([]);
  const [sent, setSent] = useState<Invite[]>([]);
  const [self, setSelf] = useState("");
  const [role, setRoleToInvite] = useState<Role>("member");
  const [copied, setCopied] = useState("");
  const [error, setError] = useState("");
  const manages = team.role === "owner" || team.role === "admin";
  // a personal team is its owner's alone — the server refuses invites into one
  const invites_ = manages && !team.personal;

  const refresh = () => {
    members(team.id).then(setWho).catch((e) => setError(String(e)));
    if (invites_) invites(team.id).then(setSent).catch((e) => setError(String(e)));
  };

  useEffect(() => {
    setError("");
    refresh();
    me().then((p) => setSelf(p.id));
  }, [team.id]);

  const act = (work: Promise<unknown>) => {
    setError("");
    work.catch((e: Error) => setError(e.message.replace(/^\d{3} /, ""))).finally(refresh);
  };

  const link = (token: string) => `${window.location.origin}/?invite=${token}`;

  return (
    <section className="card">
      <h2>Members</h2>
      <p>
        {team.personal
          ? "Your own team, yours alone. To work with others, create a team from the team menu and invite them there."
          : "Everyone here can read and add to this team's bundles."}
      </p>

      {error && <div className="banner">{error}</div>}

      <ul className="members">
        {who.map((m) => (
          <li key={m.sub}>
            <span className="who">
              <b>{m.name || m.email || m.sub}</b>
              {m.name && m.email && <span className="soft"> {m.email}</span>}
              {m.sub === self && <span className="soft"> (you)</span>}
            </span>
            {manages && m.sub !== self ? (
              <select
                aria-label={`Role of ${m.name || m.sub}`}
                value={m.role}
                onChange={(e) => act(setRole(team.id, m.sub, e.target.value as Role))}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            ) : (
              <span className="role">{m.role}</span>
            )}
            {/* leaving is the Team card's: it takes you home afterwards, which this
                list cannot — it would only refresh a team you are no longer in */}
            {manages && m.sub !== self && (
              <button className="link" onClick={() => act(removeMember(team.id, m.sub))}>
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>

      {invites_ && (
        <>
          <div className="field">
            <span>Invite someone as</span>
            <select value={role} onChange={(e) => setRoleToInvite(e.target.value as Role)}>
              {ROLES.filter((r) => team.role === "owner" || r !== "owner").map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <button className="lint" onClick={() => act(createInvite(team.id, role))}>
              Make an invite link
            </button>
          </div>
          {sent.length > 0 && (
            <ul className="members">
              {sent.map((i) => (
                <li key={i.token} className={i.state}>
                  <span className="who">
                    <code className="path">{link(i.token)}</code>
                    <span className="soft"> as {i.role}</span>
                  </span>
                  {i.state === "used" && (
                    <span className="role">
                      used{i.accepted_name ? ` by ${i.accepted_name}` : ""}
                      {i.accepted_at ? ` · ${new Date(i.accepted_at).toLocaleDateString()}` : ""}
                    </span>
                  )}
                  {i.state === "expired" && <span className="role">expired</span>}
                  {i.state === "open" && (
                    <>
                      <button
                        className="link"
                        title="Copy the link"
                        onClick={() => {
                          navigator.clipboard.writeText(link(i.token));
                          setCopied(i.token);
                        }}
                      >
                        {copied === i.token ? "copied" : <Copy />}
                      </button>
                      <button
                        className="link"
                        onClick={() => act(revokeInvite(team.id, i.token))}
                      >
                        Revoke
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className="soft">A link works once and for a week. Send it however you like.</p>
        </>
      )}
    </section>
  );
}
