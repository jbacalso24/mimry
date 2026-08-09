package com.app;

public class User extends Principal {
    private String name;

    public String getName() {
        return name;
    }

    public void refresh() {
        getName();
    }
}
