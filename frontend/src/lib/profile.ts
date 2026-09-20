import { useQuery } from "@tanstack/react-query"

import { ApiError, api } from "./api"
import { useSession } from "./session"

export function useProfile() {
  const session = useSession()
  return useQuery({
    queryKey: ["profile"],
    queryFn: api.profile,
    enabled: !!session.data,
  })
}

export function useOptions() {
  const session = useSession()
  return useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    enabled: !!session.data,
  })
}

export function useRepos() {
  const session = useSession()
  return useQuery({
    queryKey: ["repos"],
    queryFn: async () => {
      try {
        return await api.repos()
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          return { installations: [], repositories: [] }
        throw e
      }
    },
    enabled: !!session.data,
  })
}
