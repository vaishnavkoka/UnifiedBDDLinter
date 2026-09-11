Feature: Gestion des utilisateurs

  Scenario: L utilisateur enregistre se connecte correctement
    Given un utilisateur enregistre existe
    When il saisit ses identifiants
    Then il accede a son tableau de bord
