export default function ErrorMessage({ message }: { message: string }) {
  return (
    <div className="text-center py-10 px-5 text-text-dim text-sm">
      {message}
    </div>
  )
}
