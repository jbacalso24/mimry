<?php

namespace App;

class Session extends BaseSession
{
    public function renew(): bool
    {
        return $this->extend();
    }

    public function extend(): bool
    {
        return true;
    }
}
