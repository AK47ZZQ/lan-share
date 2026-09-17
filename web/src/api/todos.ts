// 待办与家庭清单域（v1.1 效率舱 M1）：清单/待办 CRUD。
import { handle, jpost } from './core'

export interface TodoMember { id: number; username: string; nickname: string }
export interface TodoListInfo {
  id: number; name: string; kind: 'personal' | 'family'
  owner_id: number; mine: boolean; open_count: number
}
export interface TodoItem {
  id: number; list_id: number; list_name: string; list_kind: string
  list_owner_id: number; title: string; notes: string
  due_at: number | null; priority: number; assignee_id: number | null
  status: 'todo' | 'done'; done_at: number | null; done_by: number | null
  created_at: number
}

export const todosApi = {
  lists: () => fetch('/api/todos/lists').then(r => handle<{ lists: TodoListInfo[]; members: TodoMember[] }>(r)),
  all: () => fetch('/api/todos').then(r => handle<{ todos: TodoItem[]; members: TodoMember[]; server_time: number }>(r)),
  createList: (name: string, kind: string) =>
    jpost('/api/todos/lists', { name, kind }).then(r => handle<{ id: number }>(r)),
  deleteList: (id: number) =>
    fetch(`/api/todos/lists/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
  create: (list_id: number, title: string) =>
    jpost('/api/todos', { list_id, title }).then(r => handle<{ id: number }>(r)),
  patch: (id: number, data: Partial<Pick<TodoItem, 'status' | 'title' | 'priority' | 'due_at' | 'assignee_id'>>) =>
    jpost(`/api/todos/${id}`, data).then(r => handle<{ ok: boolean }>(r)),
  remove: (id: number) =>
    fetch(`/api/todos/${id}`, { method: 'DELETE' }).then(r => handle<{ ok: boolean }>(r)),
}
