import type { MenuCategory } from '../../api/types'

interface Props {
  categories: MenuCategory[]
  activeId: number | null
  onSelect: (id: number) => void
}

export function CategoryNav({ categories, activeId, onSelect }: Props) {
  return (
    <nav
      aria-label="Menu categories"
      className="sticky top-0 z-20 -mx-4 border-b border-rule bg-paper/95 px-4 backdrop-blur"
    >
      <ul className="flex gap-1 overflow-x-auto py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {categories.map((c) => {
          const current = c.id === activeId
          return (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={current ? 'true' : undefined}
                className={`min-h-11 whitespace-nowrap rounded-full px-4 text-[0.9375rem] font-bold ${
                  current ? 'bg-ink text-paper' : 'text-ink-soft hover:text-ink'
                }`}
              >
                {c.name}
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
