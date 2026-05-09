export function Skeleton({
  className = "",
  width,
  height = 14,
}: {
  className?: string;
  width?: number | string;
  height?: number | string;
}) {
  const style = {
    width: typeof width === "number" ? `${width}px` : width,
    height: typeof height === "number" ? `${height}px` : height,
  };
  return <div className={`skeleton ${className}`} style={style} aria-hidden />;
}

export function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <Skeleton width="80%" />
        </td>
      ))}
    </tr>
  );
}
