import { count, funnelStages, percent } from './format.js';

export default function Funnel({ metrics }) {
  const stages = funnelStages(metrics);
  const max = Math.max(metrics.sent, 1);
  return (
    <ol className="funnel" aria-label="How many emails reached each stage">
      {stages.map((stage) => {
        const width = (stage.value / max) * 100;
        return (
          <li className={`funnel-row funnel-${stage.key}`} key={stage.key}>
            <span className="funnel-label">{stage.label}</span>
            <span className="funnel-track">
              <span className="funnel-bar" style={{ width: stage.value ? `max(${width}%, 3px)` : 0 }} />
            </span>
            <span className="funnel-value">{count(stage.value)}</span>
            <span className="funnel-rate">
              {stage.rate === null ? 'None yet' : (
                <>
                  {stage.rate !== undefined && <strong>{percent(stage.rate)} </strong>}
                  {stage.note}
                </>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
