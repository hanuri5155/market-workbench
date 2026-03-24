export default function DataTable({ columns, rows, getTone }) {
  return (
    <div className="ui-v3-table-wrap">
      <table className="ui-v3-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={row.id ?? `${row.symbol ?? row.strategy ?? "row"}-${rowIndex}`}>
              {columns.map((column) => {
                const tone = getTone?.(row, column.key);
                return (
                  <td key={column.key} className={tone ? `is-${tone}` : ""}>
                    {row[column.key]}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
