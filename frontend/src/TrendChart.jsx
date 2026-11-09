import { useEffect, useMemo, useRef, useState } from 'react';

import { count, shortDate } from './format.js';

const SERIES = [
  { key: 'opened', label: 'Opened', className: 'series-1' },
  { key: 'clicked', label: 'Clicked', className: 'series-2' },
  { key: 'replied', label: 'Replied', className: 'series-3' },
];
const HEIGHT = 260;
const MARGIN = { top: 14, right: 70, bottom: 26, left: 40 };

function niceMax(value) {
  if (value <= 4) return 4;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((n) => n * power >= value / 4) * power;
  return Math.ceil(value / step) * step;
}

/** Keep end labels apart, and inside the plot when series all finish near zero. */
export function spreadLabels(positions, maxY = Infinity, gap = 15) {
  const sorted = [...positions].sort((a, b) => a.y - b.y).map((item) => ({ ...item }));
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].y - sorted[i - 1].y < gap) sorted[i].y = sorted[i - 1].y + gap;
  }
  const overflow = sorted[sorted.length - 1].y - maxY;
  if (overflow > 0) {
    sorted[sorted.length - 1].y = maxY;
    for (let i = sorted.length - 2; i >= 0; i -= 1) {
      sorted[i].y = Math.min(sorted[i].y, sorted[i + 1].y - gap);
    }
  }
  return sorted;
}

export default function TrendChart({ rows }) {
  const wrapper = useRef(null);
  const [width, setWidth] = useState(720);
  const [active, setActive] = useState(null);
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    if (!wrapper.current || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(entry.contentRect.width);
    });
    observer.observe(wrapper.current);
    return () => observer.disconnect();
  }, []);

  const scale = useMemo(() => {
    const innerWidth = Math.max(width - MARGIN.left - MARGIN.right, 40);
    const innerHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
    const top = niceMax(Math.max(1, ...rows.flatMap((row) => SERIES.map((s) => row[s.key]))));
    return {
      x: (index) => MARGIN.left + (rows.length > 1 ? (index / (rows.length - 1)) * innerWidth : 0),
      y: (value) => MARGIN.top + innerHeight - (value / top) * innerHeight,
      top,
      innerWidth,
    };
  }, [rows, width]);

  // The wrapper stays mounted in both states, because the resize observer is tied to it.
  if (!rows.some((row) => SERIES.some((series) => row[series.key] > 0))) {
    return (
      <div className="trend" ref={wrapper}>
        <p className="chart-empty">Opens, clicks and replies appear here after the first emails go out.</p>
      </div>
    );
  }

  const paths = SERIES.map((series) => ({
    ...series,
    d: rows.map((row, i) => `${i ? 'L' : 'M'}${scale.x(i).toFixed(1)} ${scale.y(row[series.key]).toFixed(1)}`).join(''),
  }));
  const last = rows.length - 1;
  const endLabels = spreadLabels(SERIES.map((s) => ({ ...s, y: scale.y(rows[last][s.key]) + 4 })), HEIGHT - MARGIN.bottom + 4);
  const ticks = [0, 0.5, 1].map((part) => part * scale.top);
  const labelEvery = Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor(scale.innerWidth / 90))));

  function pick(event) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left - MARGIN.left) / scale.innerWidth;
    setActive(Math.min(last, Math.max(0, Math.round(ratio * last))));
  }

  function onKeyDown(event) {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const delta = event.key === 'ArrowLeft' ? -1 : 1;
      setActive((value) => Math.min(last, Math.max(0, (value ?? last) + delta)));
    } else if (event.key === 'Escape') {
      setActive(null);
    }
  }

  const row = active === null ? null : rows[active];
  const tooltipLeft = row
    ? Math.min(Math.max(scale.x(active) + 12, 0), Math.max(width - 170, 0))
    : 0;

  return (
    <div className="trend" ref={wrapper}>
      <ul className="legend" aria-hidden="true">
        {SERIES.map((series) => (
          <li key={series.key}>
            <svg width="18" height="8"><line x1="0" x2="18" y1="4" y2="4" className={series.className} /></svg>
            {series.label}
          </li>
        ))}
      </ul>
      <div className="trend-plot">
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          tabIndex={0}
          aria-label="Daily opens, clicks and replies. Use the left and right arrow keys to read each day."
          onPointerMove={pick}
          onPointerLeave={() => setActive(null)}
          onKeyDown={onKeyDown}
          onBlur={() => setActive(null)}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line className="grid" x1={MARGIN.left} x2={MARGIN.left + scale.innerWidth} y1={scale.y(tick)} y2={scale.y(tick)} />
              <text className="axis" x={MARGIN.left - 8} y={scale.y(tick) + 4} textAnchor="end">{count(tick)}</text>
            </g>
          ))}
          {rows.map((item, i) => (i % labelEvery === 0 ? (
            <text key={item.day} className="axis" x={scale.x(i)} y={HEIGHT - 6} textAnchor={i === 0 ? 'start' : 'middle'}>
              {shortDate(item.day)}
            </text>
          ) : null))}
          {paths.map((series) => <path key={series.key} d={series.d} className={`line ${series.className}`} />)}
          {endLabels.map((label) => (
            <text key={label.key} className="end-label" x={MARGIN.left + scale.innerWidth + 8} y={label.y}>
              {label.label}
            </text>
          ))}
          {row && (
            <g>
              <line className="crosshair" x1={scale.x(active)} x2={scale.x(active)} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} />
              {SERIES.map((series) => (
                <circle key={series.key} className={`dot ${series.className}`} cx={scale.x(active)} cy={scale.y(row[series.key])} r="4" />
              ))}
            </g>
          )}
        </svg>
        {row && (
          <div className="tooltip" style={{ left: tooltipLeft }} role="status">
            <div className="tooltip-day">{shortDate(row.day)}</div>
            {SERIES.map((series) => (
              <div className="tooltip-row" key={series.key}>
                <svg width="12" height="6" aria-hidden="true"><line x1="0" x2="12" y1="3" y2="3" className={series.className} /></svg>
                <strong>{count(row[series.key])}</strong> {series.label.toLowerCase()}
              </div>
            ))}
            <div className="tooltip-extra">{count(row.sent)} sent, {count(row.bounced)} bounced</div>
          </div>
        )}
      </div>
      <button type="button" className="text-button" aria-expanded={showTable} onClick={() => setShowTable(!showTable)}>
        {showTable ? 'Hide daily numbers' : 'Show daily numbers'}
      </button>
      {showTable && (
        <div className="table-scroll trend-table">
          <table>
            <thead>
              <tr><th scope="col">Day</th><th scope="col" className="num">Sent</th><th scope="col" className="num">Bounced</th><th scope="col" className="num">Opened</th><th scope="col" className="num">Clicked</th><th scope="col" className="num">Replied</th></tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.day}>
                  <th scope="row">{shortDate(item.day)}</th>
                  {['sent', 'bounced', 'opened', 'clicked', 'replied'].map((key) => <td className="num" key={key}>{count(item[key])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
