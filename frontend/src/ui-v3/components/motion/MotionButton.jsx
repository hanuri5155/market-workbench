export default function MotionButton({
  as: Component = "button",
  className = "",
  children,
  ...props
}) {
  const classes = ["ui-v3-motion-button", className].filter(Boolean).join(" ");
  return (
    <Component className={classes} {...props}>
      {children}
    </Component>
  );
}
