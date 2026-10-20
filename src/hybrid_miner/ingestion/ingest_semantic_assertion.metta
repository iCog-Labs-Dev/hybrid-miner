; Evaluate emitted assertion requirements against PeTTa fact and metadata spaces.
; Raw JSON structure and source-text offset bounds are validated before import.

(= (ingress-present? $space $atom)
   (case (match $space $atom True)
         ((True True)
          (Empty False))))

(= (ingress-requirement-valid? (fact $atom))
   (ingress-present? &facts $atom))
(= (ingress-requirement-valid? (meta $atom))
   (ingress-present? &meta $atom))
(= (ingress-requirement-valid? (schema $atom))
   (ingress-present? &schema $atom))

(= (ingress-all-valid? ()) True)
(= (ingress-all-valid? (cons $requirement $rest))
   (if (ingress-requirement-valid? $requirement)
       (ingress-all-valid? $rest)
       False))

(= (ingest-semantic-assertion $id $atom $requirements)
   (if (ingress-all-valid? $requirements)
       (add-atom &accepted (IngressAccepted $id $atom))
       (add-atom &quarantine (IngressQuarantined $id $atom))))
