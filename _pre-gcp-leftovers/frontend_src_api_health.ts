export type HealthResponse = {
  status: string
  message: string
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health')

  if (!response.ok) {
    throw new Error(`Health check failed (${response.status})`)
  }

  return response.json() as Promise<HealthResponse>
}
