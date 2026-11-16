import { type LabResults, type ResultContrast, type Scale, along, byKpi, scaleOf, signed } from "../lib/results";

const PLOT_W = 260;
const PLOT_H = 22;

const KPI_LABELS: Record<string, string> = {
  completion_mean_s: "Mean completion time (s)",
  completion_p95_s: "95th percentile completion time (s)",
  completion_max_s: "Longest completion time (s)",
  waiting_mean_s: "Mean waiting time (s)",
  waiting_p95_s: "95th percentile waiting time (s)",
  throughput_per_h: "Throughput (orders/h)",
  utilization: "Utilization",
};

function p(value: number | null): string {
  if (value === null) return "–";
  return value < 0.001 ? "< 0.001" : value.toFixed(3);
}

/** The difference and its interval on the group's shared axis, with zero and the margin. */
function IntervalPlot({ contrast, scale, margin }: { contrast: ResultContrast; scale: Scale; margin: number }) {
  const x = (value: number): number => along(value, scale) * PLOT_W;
  const mid = PLOT_H / 2;
  const interval =
    contrast.low === null || contrast.high === null
      ? "no interval"
      : `interval ${signed(contrast.low)} to ${signed(contrast.high)}`;
  return (
    <svg
      className={`interval ${contrast.verdict}`}
      width={PLOT_W}
      height={PLOT_H}
      viewBox={`0 0 ${PLOT_W} ${PLOT_H}`}
      role="img"
      aria-label={`Difference ${signed(contrast.difference)}, ${interval}`}
    >
      {margin > 0 && <rect className="margin" x={x(-margin)} y={2} width={x(margin) - x(-margin)} height={PLOT_H - 4} />}
      <line className="zero" x1={x(0)} x2={x(0)} y1={0} y2={PLOT_H} />
      {contrast.low !== null && contrast.high !== null && (
        <line className="whisker" x1={x(contrast.low)} x2={x(contrast.high)} y1={mid} y2={mid} />
      )}
      <circle className="point" cx={x(contrast.difference)} cy={mid} r={4} />
    </svg>
  );
}

export function ResultsPage({
  results,
  label,
  onClose,
}: {
  results: LabResults;
  label: string;
  onClose: () => void;
}) {
  const percent = `${Math.round(results.confidence * 100)}%`;
  return (
    <main className="results">
      <div className="results-head">
        <div>
          <h2>{results.name}</h2>
          <p className="muted small">
            {label}
            {results.commit !== null && (
              <>
                {" "}
                · engine commit <code>{results.commit.slice(0, 7)}</code>
              </>
            )}
          </p>
        </div>
        <button className="secondary" type="button" onClick={onClose}>
          Back to the replay
        </button>
      </div>

      <dl className="results-facts">
        <div>
          <dt>Compared</dt>
          <dd>
            {results.policies.filter((policy) => policy !== results.baseline).join(", ")} against{" "}
            <strong>{results.baseline}</strong>
          </dd>
        </div>
        <div>
          <dt>Replication</dt>
          <dd>
            {results.seeds} seeds, paired · {results.runs} runs
          </dd>
        </div>
        <div>
          <dt>Disturbances</dt>
          <dd>{results.model === "schedule" ? "fixed schedule per seed" : "independent draws per run"}</dd>
        </div>
        <div>
          <dt>Equivalence margin</dt>
          <dd>±{results.margin} on the primary KPI</dd>
        </div>
        <div>
          <dt>Runs with undelivered orders</dt>
          <dd className={results.undelivered > 0 ? "flag" : undefined}>{results.undelivered}</dd>
        </div>
        <div>
          <dt>Runs beyond their schedule</dt>
          <dd className={results.beyondHorizon > 0 ? "flag" : undefined}>{results.beyondHorizon}</dd>
        </div>
      </dl>

      <ul className="results-families">
        {results.families.map((family) => (
          <li key={family.name}>
            <code>{family.name}</code> map {family.map}, {family.robots} robots, {family.ordersPerHour} orders/h
          </li>
        ))}
      </ul>

      {byKpi(results).map((group) => {
        const scale = scaleOf(group.contrasts, results.margin);
        return (
          <section key={group.kpi} className="results-group">
            <h3>
              {KPI_LABELS[group.kpi] ?? group.kpi}
              {group.primary && <span className="primary-tag">primary</span>}
            </h3>
            <div className="results-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Family / policy</th>
                    <th scope="col" className="num">
                      Policy
                    </th>
                    <th scope="col" className="num">
                      Baseline
                    </th>
                    <th scope="col" className="num">
                      Difference [{percent} interval]
                    </th>
                    <th scope="col">
                      {signed(scale.min, 0)} … {signed(scale.max, 0)}
                    </th>
                    <th scope="col" className="num" title="Seeds where the policy did better, worse, the same">
                      W–L–T
                    </th>
                    <th scope="col" className="num" title="Holm-corrected on the primary KPI; signed-rank otherwise">
                      p
                    </th>
                    <th scope="col" className="num" title="Correlation between the two policies across seeds">
                      Corr.
                    </th>
                    <th scope="col">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {group.contrasts.map((contrast) => (
                    <tr key={`${contrast.family}/${contrast.policy}`}>
                      <th scope="row">
                        <code>{contrast.family}</code> {contrast.policy}
                      </th>
                      <td className="num">{contrast.meanVariant.toFixed(1)}</td>
                      <td className="num">{contrast.meanBaseline.toFixed(1)}</td>
                      <td className="num">
                        {signed(contrast.difference)}{" "}
                        <span className="muted">
                          {contrast.low === null || contrast.high === null
                            ? "[–]"
                            : `[${signed(contrast.low)}, ${signed(contrast.high)}]`}
                        </span>
                      </td>
                      <td>
                        <IntervalPlot contrast={contrast} scale={scale} margin={results.margin} />
                      </td>
                      <td className="num">
                        {contrast.wins}–{contrast.losses}–{contrast.ties}
                      </td>
                      <td className="num">{p(group.primary ? contrast.holmP : contrast.signedRankP)}</td>
                      <td className="num">{contrast.correlation === null ? "–" : contrast.correlation.toFixed(2)}</td>
                      <td>
                        <span className={`verdict ${contrast.verdict}`}>{contrast.verdict}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}

      <p className="results-note muted small">
        Every figure is read from the results file; this page computes none. A difference is the policy minus the
        baseline, averaged over seeds. The dot is the difference, the bar its {percent} interval, the line is zero and
        the band is the equivalence margin. “Inconclusive” means the interval neither clears zero nor fits inside the
        margin: it is not evidence that the policies are equal.
      </p>
    </main>
  );
}
