package store

type Store struct {
	name string
}

func NewStore() *Store {
	return &Store{name: "sessions"}
}
