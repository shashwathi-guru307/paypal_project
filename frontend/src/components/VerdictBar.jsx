/**
 * The backend serialises AIVerdict with `by_alias=True`, so the boolean arrives
 * as `pass`. `passed` is accepted too so the component survives either shape.
 */
export function verdictPassed(verdict) {
  if (!verdict) return false;
  return Boolean(verdict.pass ?? verdict.passed);
}

/** True when the model was unreachable and llm.py returned its fallback verdict. */
export function isFallbackVerdict(verdict) {
  if (!verdict) return false;
  return Number(verdict.score) === 0 && /manual review/i.test(verdict.reason || "");
}

export default function VerdictBar({ verdict }) {
  if (!verdict) return null;

  const score = Math.max(0, Math.min(100, Math.round(Number(verdict.score) || 0)));
  const passed = verdictPassed(verdict);
  const missing = Array.isArray(verdict.missing) ? verdict.missing : [];

  return (
    <div className={`verdict ${passed ? "verdict-pass" : "verdict-fail"}`}>
      <div className="verdict-head">
        <span className="verdict-label">{passed ? "PASS" : "FAIL"}</span>
        <span className="verdict-score">{score}/100</span>
      </div>

      <div className="verdict-track" role="img" aria-label={`AI score ${score} out of 100`}>
        <div className="verdict-fill" style={{ width: `${score}%` }} />
      </div>

      <p className="verdict-reason">{verdict.reason}</p>

      {missing.length > 0 ? (
        <div className="verdict-missing">
          <h4>Missing or weak</h4>
          <ul>
            {missing.map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
