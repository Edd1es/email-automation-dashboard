import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';

import { count, funnelStages, generatedLabel, headline, percent } from './format.js';
import Funnel from './Funnel.jsx';
import TrendChart, { spreadLabels } from './TrendChart.jsx';

afterEach(cleanup);

const metrics = {
  sent: 100, delivered: 96, opened: 40, clicked: 10, replied: 5,
  delivery_rate: 0.96, opened_rate: 0.4167, clicked_rate: 0.1042, replied_rate: 0.0521,
};

describe('format', () => {
  test('percent treats no data differently from zero', () => {
    expect(percent(null)).toBe('None yet');
    expect(percent(0)).toBe('0.0%');
    expect(percent(0.9612)).toBe('96.1%');
  });

  test('count adds separators', () => {
    expect(count(1234)).toBe('1,234');
  });

  test('headline reads as a sentence in each case', () => {
    expect(headline(metrics)).toBe('Of 100 emails sent, 96 arrived and 5 people replied.');
    expect(headline({ sent: 0 })).toBe('Nothing has been sent yet.');
    expect(headline({ sent: 1, delivered: 1, replied: 1 })).toBe('Of 1 email sent, 1 arrived and 1 person replied.');
    expect(headline({ sent: 2, delivered: 2, replied: 0 })).toBe('Of 2 emails sent, all 2 arrived and no one has replied yet.');
  });

  test('funnel stages use delivered as the base for engagement', () => {
    const replied = funnelStages(metrics).find((stage) => stage.key === 'replied');
    expect(replied.value).toBe(5);
    expect(replied.rate).toBe(0.0521);
    expect(replied.note).toBe('of delivered');
  });

  test('generated content names its source', () => {
    expect(generatedLabel('template')).toBe('Plain template');
    expect(generatedLabel('rules')).toBe('Rule-based summary');
    expect(generatedLabel('openai/gpt-oss-120b')).toBe('Groq, openai/gpt-oss-120b');
  });
});

describe('Funnel', () => {
  test('shows every stage with its number', () => {
    render(<Funnel metrics={metrics} />);
    expect(screen.getByText('Delivered')).toBeTruthy();
    expect(screen.getByText('96')).toBeTruthy();
    expect(screen.getByText('96.0%')).toBeTruthy();
  });

  test('handles a campaign with nothing sent', () => {
    render(<Funnel metrics={{ sent: 0, delivered: 0, opened: 0, clicked: 0, replied: 0, delivery_rate: null, opened_rate: null, clicked_rate: null, replied_rate: null }} />);
    expect(screen.getAllByText('None yet').length).toBe(4);
  });
});

describe('TrendChart', () => {
  const rows = [
    { day: '2026-01-01', sent: 9, bounced: 1, opened: 3, clicked: 2, replied: 1 },
    { day: '2026-01-02', sent: 0, bounced: 0, opened: 7, clicked: 4, replied: 2 },
  ];

  test('offers the numbers as a table for people who can not read the chart', () => {
    render(<TrendChart rows={rows} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show daily numbers' }));
    expect(screen.getByRole('table')).toBeTruthy();
    expect(screen.getByRole('rowheader', { name: 'Jan 2' })).toBeTruthy();
  });

  test('arrow keys move the readout', () => {
    render(<TrendChart rows={rows} />);
    fireEvent.keyDown(screen.getByRole('img'), { key: 'ArrowLeft' });
    expect(screen.getByRole('status').textContent).toContain('Jan 1');
    expect(screen.getByRole('status').textContent).toContain('9 sent, 1 bounced');
  });

  test('end labels never overlap', () => {
    const placed = spreadLabels([{ y: 100 }, { y: 101 }, { y: 102 }]);
    expect(placed[1].y - placed[0].y).toBeGreaterThanOrEqual(15);
    expect(placed[2].y - placed[1].y).toBeGreaterThanOrEqual(15);
  });

  test('end labels stay inside the plot when every series ends at zero', () => {
    const placed = spreadLabels([{ y: 238 }, { y: 238 }, { y: 238 }], 238);
    expect(placed.map((label) => label.y)).toEqual([208, 223, 238]);
  });

  test('keeps the measured wrapper when the first data arrives', () => {
    const empty = [{ day: '2026-01-01', sent: 0, bounced: 0, opened: 0, clicked: 0, replied: 0 }];
    const { container, rerender } = render(<TrendChart rows={empty} />);
    const wrapper = container.firstChild;
    rerender(<TrendChart rows={rows} />);
    expect(container.firstChild).toBe(wrapper);
    expect(screen.getByRole('img')).toBeTruthy();
  });

  test('explains an empty chart instead of drawing flat lines', () => {
    render(<TrendChart rows={[{ day: '2026-01-01', sent: 0, bounced: 0, opened: 0, clicked: 0, replied: 0 }]} />);
    expect(screen.getByText(/appear here after the first emails/)).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });
});
