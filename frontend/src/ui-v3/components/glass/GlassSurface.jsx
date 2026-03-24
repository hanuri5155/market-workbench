export default function GlassSurface({
  as: Component = "div",
  className = "",
  level = "base",
  interactive = false,
  children,
  ...props
}) {
  const classes = [
    "ui-v3-liquid-surface",
    `ui-v3-liquid-surface--${level}`,
    interactive ? "is-interactive" : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <Component className={classes} {...props}>
      {children}
    </Component>
  );
}
