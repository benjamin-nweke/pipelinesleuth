import React from 'react';

interface DiffViewerProps {
  diff: string;
  filename?: string;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({ diff, filename }) => {
  const lines = diff.split('\n');

  return (
    <div className="rounded-lg border border-charcoal-750 bg-charcoal-900 font-mono text-xs overflow-hidden">
      {filename && (
        <div className="bg-charcoal-850 px-4 py-2 border-b border-charcoal-750 flex items-center justify-between text-charcoal-300">
          <span className="font-medium text-charcoal-200">{filename}</span>
          <span className="text-[11px] text-charcoal-400">Unified Diff</span>
        </div>
      )}
      <div className="overflow-x-auto p-2">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((line, idx) => {
              const isAdd = line.startsWith('+') && !line.startsWith('+++');
              const isDel = line.startsWith('-') && !line.startsWith('---');
              const isHeader = line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++');

              let rowClass = 'hover:bg-charcoal-800/40 text-charcoal-300';
              let lineBadge = ' ';
              if (isAdd) {
                rowClass = 'bg-emerald-950/40 text-emerald-300 border-l-2 border-emerald-500';
                lineBadge = '+';
              } else if (isDel) {
                rowClass = 'bg-rose-950/40 text-rose-300 border-l-2 border-rose-500 line-through';
                lineBadge = '-';
              } else if (isHeader) {
                rowClass = 'bg-charcoal-800/60 text-sleuth-amber-400 font-semibold';
              }

              return (
                <tr key={idx} className={`leading-5 ${rowClass}`}>
                  <td className="w-10 select-none text-right pr-3 text-charcoal-500 font-mono text-[11px]">
                    {idx + 1}
                  </td>
                  <td className="w-4 select-none text-center font-bold">
                    {lineBadge}
                  </td>
                  <td className="whitespace-pre font-mono pl-2">
                    {line.substring(line.startsWith('+') || line.startsWith('-') ? 1 : 0) || ' '}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
